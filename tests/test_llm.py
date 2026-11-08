"""Image resizing and Groq provider behaviour (fallback, retries) with a fake client."""
import io
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image

from app import llm
from app.config import settings
from app.models import VisionResult


class FakeErr(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status_code = status


def _resp(content="", tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


class FakeClient:
    def __init__(self, handler):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.handler = handler

    def _create(self, **kw):
        self.calls.append(kw)
        return self.handler(kw, len(self.calls))


@pytest.fixture()
def fake(monkeypatch):
    monkeypatch.setattr(llm, "BACKOFF_BASE", 0)
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test")

    def install(handler):
        c = FakeClient(handler)
        monkeypatch.setattr(llm, "_client", c)
        return c
    return install


GOOD = json.dumps({"items_seen": [], "items_missing": [{"name": "Fries", "qty_missing": 1, "confidence": 0.9}],
                   "wrong_items": [], "quality_issue": {"present": False, "description": ""}, "is_food_photo": True,
                   "overall_confidence": 0.9, "short_reason": "ok"})


def _png(w=200, h=100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "red").save(buf, format="PNG")
    return buf.getvalue()


# ---- resizing --------------------------------------------------------------
def test_resize_large_noisy_image_under_limit():
    big = Image.frombytes("RGB", (4000, 3000), os.urandom(4000 * 3000 * 3))  # noise compresses badly
    buf = io.BytesIO()
    big.save(buf, format="PNG")
    out = llm.prepare_image(buf.getvalue())
    assert len(out) <= 3.5 * 1024 * 1024
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and max(img.size) <= 1280


def test_resize_tight_limit_steps_quality_and_size():
    noisy = Image.frombytes("RGB", (1000, 1000), os.urandom(1000 * 1000 * 3))
    buf = io.BytesIO()
    noisy.save(buf, format="PNG")
    out = llm.prepare_image(buf.getvalue(), max_bytes=60_000)
    assert len(out) <= 60_000


def test_resize_keeps_small_image_size_and_converts_rgba():
    buf = io.BytesIO()
    Image.new("RGBA", (300, 200), (0, 0, 0, 0)).save(buf, format="PNG")
    img = Image.open(io.BytesIO(llm.prepare_image(buf.getvalue())))
    assert img.size == (300, 200) and img.mode == "RGB"


# ---- vision fallback -------------------------------------------------------
def test_vision_falls_back_when_model_missing(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_VISION_MODEL", "qwen/missing")
    monkeypatch.setattr(settings, "GROQ_VISION_FALLBACK_MODEL", "meta-llama/scout")

    def handler(kw, n):
        if kw["model"] == "qwen/missing":
            raise FakeErr(404, "The model `qwen/missing` does not exist (model_not_found)")
        return _resp(GOOD)
    c = fake(handler)
    res, used, ms = llm.vision_json(_png(), "prompt", VisionResult)
    assert used == "meta-llama/scout" and res.items_missing[0].name == "Fries"
    assert [k["model"] for k in c.calls] == ["qwen/missing", "meta-llama/scout"]
    assert c.calls[-1]["response_format"] == {"type": "json_object"}
    content = c.calls[-1]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vision_falls_back_when_decommissioned(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_VISION_MODEL", "old")
    monkeypatch.setattr(settings, "GROQ_VISION_FALLBACK_MODEL", "new")
    fake(lambda kw, n: (_ for _ in ()).throw(FakeErr(400, "model_decommissioned")) if kw["model"] == "old" else _resp(GOOD))
    assert llm.vision_json(_png(), "p", VisionResult)[1] == "new"


def test_vision_broken_json_retries_once_then_raises(fake):
    c = fake(lambda kw, n: _resp("not json at all"))
    with pytest.raises(llm.VisionParseError):
        llm.vision_json(_png(), "p", VisionResult, model="m")
    assert len(c.calls) == 2


def test_vision_broken_then_good(fake):
    fake(lambda kw, n: _resp("oops") if n == 1 else _resp("<think>hmm</think>" + GOOD))
    res, used, _ = llm.vision_json(_png(), "p", VisionResult, model="m")
    assert res.overall_confidence == 0.9


# ---- retries ---------------------------------------------------------------
def test_retries_on_429_then_succeeds_and_notifies(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_CHAT_FALLBACK_MODEL", "")
    notes = []
    c = fake(lambda kw, n: (_ for _ in ()).throw(FakeErr(429, "rate limited")) if n < 3 else _resp("hi"))
    out = llm.chat_with_tools([{"role": "user", "content": "x"}], [], on_retry=lambda a, e: notes.append(a))
    assert out["content"] == "hi" and len(c.calls) == 3 and notes == [1, 2]


def test_gives_up_after_3_retries(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_CHAT_FALLBACK_MODEL", "")
    c = fake(lambda kw, n: (_ for _ in ()).throw(FakeErr(503, "down")))
    with pytest.raises(FakeErr):
        llm.chat_with_tools([{"role": "user", "content": "x"}], [])
    assert len(c.calls) == 4  # 1 try + 3 retries


def test_no_retry_on_400(fake):
    c = fake(lambda kw, n: (_ for _ in ()).throw(FakeErr(400, "bad request")))
    with pytest.raises(FakeErr):
        llm.chat_with_tools([{"role": "user", "content": "x"}], [])
    assert len(c.calls) == 1


def test_chat_switches_to_fallback_on_429(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_CHAT_MODEL", "big")
    monkeypatch.setattr(settings, "GROQ_CHAT_FALLBACK_MODEL", "small")
    c = fake(lambda kw, n: (_ for _ in ()).throw(FakeErr(429, "tpm")) if kw["model"] == "big" else _resp("ok"))
    out = llm.chat_with_tools([{"role": "user", "content": "x"}], [])
    assert out["model"] == "small" and [k["model"] for k in c.calls] == ["big", "small"]


def test_chat_passes_reasoning_effort_and_hides_reasoning(fake, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_CHAT_MODEL", "openai/gpt-oss-120b")
    c = fake(lambda kw, n: _resp("<think>secret</think>Hello"))
    out = llm.chat_with_tools([{"role": "user", "content": "x"}], [{"type": "function"}])
    assert out["content"] == "Hello"
    kw = c.calls[0]
    assert kw["reasoning_effort"] == "low" and kw["include_reasoning"] is False and kw["tool_choice"] == "auto"


def test_retry_after_parsing():
    assert llm.retry_after(Exception("Please try again in 3.8s.")) == 3.8
    assert llm.retry_after(Exception("try again in 250ms")) == 0.25
    assert llm.retry_after(Exception("nothing")) == 0.0
