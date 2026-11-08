"""Thin Groq provider layer. The only module that imports groq.

    chat_with_tools(messages, tools)        -> {"content", "tool_calls", "model"}
    vision_json(image, prompt, schema)      -> (validated pydantic object, model_used, latency_ms)

Both retry 429/5xx with exponential backoff (max 3 retries) and fall back to a second model
when the first one is missing or decommissioned. Swap providers by rewriting this file only.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import ssl
import time
from typing import Any, Callable

import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger("swish.llm")

MAX_RETRIES = 3
BACKOFF_BASE = 0.6  # seconds; tests set this to 0
MAX_IMAGE_BYTES = int(3.5 * 1024 * 1024)
MAX_SIDE = 1280

OnRetry = Callable[[int, Exception], None] | None


class LLMError(Exception):
    pass


class VisionParseError(LLMError):
    """The vision model did not return valid JSON for the schema (after one retry)."""


_client = None


def ssl_context() -> ssl.SSLContext:
    """Trust the OS certificate store plus certifi.

    Some laptops (antivirus / corporate proxies) re-sign TLS traffic with a local root that only the
    OS store knows; servers like Render only have certifi/system bundles. This works in both.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001
        pass
    return ctx


def http_client(timeout: float = 45) -> httpx.Client:
    return httpx.Client(verify=ssl_context(), timeout=timeout, follow_redirects=True)


def client():
    global _client
    if _client is None:
        if not settings.GROQ_API_KEY:
            raise LLMError("GROQ_API_KEY is not set")
        import groq

        _client = groq.Groq(api_key=settings.GROQ_API_KEY, max_retries=0, timeout=45, http_client=http_client())
    return _client


def reset_client() -> None:
    global _client
    _client = None


# ---------------------------------------------------------------- error helpers
def _status(err: Exception) -> int | None:
    return getattr(err, "status_code", None) or getattr(getattr(err, "response", None), "status_code", None)


def is_retryable(err: Exception) -> bool:
    s = _status(err)
    if s is not None:
        return s == 429 or s >= 500
    name = type(err).__name__
    return name in ("APIConnectionError", "APITimeoutError")


def is_model_missing(err: Exception) -> bool:
    s = _status(err)
    text = str(err).lower()
    if s == 404:
        return True
    return any(k in text for k in ("model_not_found", "decommissioned", "does not exist", "model not found", "not supported for this model"))


def retry_after(err: Exception) -> float:
    """Seconds the server asked us to wait (header or 'try again in 3.2s' in the message)."""
    headers = getattr(getattr(err, "response", None), "headers", None) or {}
    try:
        if headers.get("retry-after"):
            return float(headers["retry-after"])
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in ([\d.]+)(ms|s)", str(err))
    if m:
        return float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
    return 0.0


def is_tool_use_failed(err: Exception) -> bool:
    return _status(err) == 400 and "tool_use_failed" in str(err)


def with_retries(fn: Callable[[], Any], on_retry: OnRetry = None, max_retries: int = MAX_RETRIES) -> Any:
    tool_retry_done = False
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as err:  # noqa: BLE001
            if is_tool_use_failed(err) and not tool_retry_done:  # model produced a malformed tool call: sample again
                tool_retry_done = True
                log.warning("Malformed tool call, retrying once: %s", err)
                continue
            if attempt >= max_retries or not is_retryable(err):
                raise
            delay = BACKOFF_BASE * (2 ** attempt)
            if BACKOFF_BASE:
                delay = min(10.0, max(delay, retry_after(err) + 0.2))
            log.warning("Groq call failed (%s), retry %d in %.1fs", err, attempt + 1, delay)
            if on_retry:
                try:
                    on_retry(attempt + 1, err)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(delay)
            attempt += 1


def _with_fallback(call: Callable[[str], Any], primary: str, fallback: str | None, on_retry: OnRetry) -> tuple[Any, str]:
    """Try the primary once. If it is missing/decommissioned, or rate limited / down, switch to the fallback
    straight away (it has its own rate limit bucket). If both are busy, back off and retry (max 3)."""
    has_fallback = bool(fallback and fallback != primary)
    try:
        return with_retries(lambda: call(primary), on_retry, max_retries=0 if has_fallback else MAX_RETRIES), primary
    except Exception as err:  # noqa: BLE001
        if not has_fallback or not (is_model_missing(err) or is_retryable(err)):
            raise
        log.warning("Model %s unavailable (%s). Falling back to %s", primary, str(err)[:200], fallback)
        if is_model_missing(err):
            return with_retries(lambda: call(fallback), on_retry), fallback
        try:
            return with_retries(lambda: call(fallback), None, max_retries=0), fallback
        except Exception as err2:  # noqa: BLE001
            if not is_retryable(err2) and not is_model_missing(err2):
                raise
            return with_retries(lambda: call(primary), on_retry), primary


# ---------------------------------------------------------------- chat
def chat_with_tools(messages: list[dict], tools: list[dict], model: str | None = None,
                    reasoning_effort: str = "low", on_retry: OnRetry = None) -> dict:
    primary = model or settings.GROQ_CHAT_MODEL

    def call(m: str):
        kwargs: dict[str, Any] = dict(model=m, messages=messages, temperature=0.3, max_completion_tokens=1024)
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        if "gpt-oss" in m:
            kwargs.update(reasoning_effort=reasoning_effort, include_reasoning=False)
        return client().chat.completions.create(**kwargs)

    resp, used = _with_fallback(call, primary, settings.GROQ_CHAT_FALLBACK_MODEL, on_retry)
    msg = resp.choices[0].message
    calls = []
    for tc in msg.tool_calls or []:
        calls.append({"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments or "{}"})
    # Only the final content is ever returned; reasoning is dropped on purpose.
    return {"content": strip_think(msg.content or ""), "tool_calls": calls, "model": used}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


# ---------------------------------------------------------------- images
def prepare_image(data: bytes, max_side: int = MAX_SIDE, quality: int = 85, max_bytes: int = MAX_IMAGE_BYTES) -> bytes:
    """Resize to max side 1280px, JPEG q85, step quality down until under 3.5 MB."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB",):
        img = img.convert("RGB")
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    q = quality
    while True:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=q, optimize=True)
        out = buf.getvalue()
        if len(out) <= max_bytes:
            return out
        if q > 35:
            q -= 10
        else:  # still too big: shrink dimensions
            img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))), Image.Resampling.LANCZOS)


def _extract_json(text: str) -> Any:
    text = strip_think(text)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if not m:
            raise
        return json.loads(m.group(0))


# ---------------------------------------------------------------- vision
def vision_json(image: bytes, prompt: str, schema: type[BaseModel], model: str | None = None,
                on_retry: OnRetry = None) -> tuple[BaseModel, str, int]:
    jpeg = prepare_image(image)
    data_url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}]
    primary = model or settings.GROQ_VISION_MODEL
    fallback = settings.GROQ_VISION_FALLBACK_MODEL if model is None else None

    def call(m: str):
        kwargs: dict[str, Any] = dict(model=m, messages=messages, temperature=0.1, max_completion_tokens=1200,
                                      response_format={"type": "json_object"})
        if "qwen" in m:
            kwargs["reasoning_format"] = "hidden"
        try:
            return client().chat.completions.create(**kwargs)
        except Exception as err:  # noqa: BLE001
            if "reasoning" in str(err).lower() and "reasoning_format" in kwargs:
                kwargs.pop("reasoning_format")
                return client().chat.completions.create(**kwargs)
            raise

    started = time.perf_counter()
    last_err: Exception | None = None
    used = primary
    for attempt in range(2):  # broken JSON -> retry once
        resp, used = _with_fallback(call, primary, fallback, on_retry)
        primary, fallback = used, None  # stick with the model that worked
        content = resp.choices[0].message.content or ""
        try:
            obj = schema.model_validate(_extract_json(content))
            latency = int((time.perf_counter() - started) * 1000)
            return obj, used, latency
        except (ValueError, ValidationError) as err:
            last_err = err
            log.warning("Vision JSON invalid on attempt %d: %s", attempt + 1, err)
    raise VisionParseError(f"vision model returned invalid JSON twice: {last_err}")
