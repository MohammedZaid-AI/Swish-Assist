"""API tests for /api/chat and /whatsapp with Groq mocked (no keys, no network)."""
import json
import re

import pytest
from fastapi.testclient import TestClient

from app import agent, llm
from app.config import settings
from app.models import VisionResult

SAMPLE = "static/samples/burger_no_fries.jpg"


class FakeGroq:
    """Scripted chat model: decides tool calls from the last user message, like the real model would."""

    def __init__(self):
        self.calls = []

    def __call__(self, messages, tools, model=None, reasoning_effort="low", on_retry=None):
        self.calls.append(messages)
        assert all(isinstance(m.get("content"), (str, type(None))) for m in messages), "images must never reach the chat model"
        last = messages[-1]
        if last["role"] == "tool":
            result = json.loads(last["content"])
            text = result.get("suggested_reply") or result.get("error") or f"Done: {json.dumps(result)[:80]}"
            if "stage_label" in result:
                text = f"Your order {result['order_id']} is {result['stage_label'].lower()}, ETA {result['eta_minutes_remaining']} min."
            return {"content": text, "tool_calls": [], "model": "fake"}
        user = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        img = re.search(r"img_[a-f0-9]+", user)
        if img:
            return self._call("check_photo_complaint", {"image_id": img.group(0), "user_message": user, "claimed_items": ["fries"]})
        if "where" in user.lower():
            return self._call("get_order_status", {"order_id": None})
        if user.lower().startswith("yes"):
            return self._call("place_order", {})
        return {"content": "Hi! How can I help?", "tool_calls": [], "model": "fake"}

    def _call(self, name, args):
        return {"content": "", "tool_calls": [{"id": f"call_{len(self.calls)}", "name": name, "arguments": json.dumps(args)}], "model": "fake"}


VISION_CALLS = []


def fake_vision(image, prompt, schema, model=None, on_retry=None):
    VISION_CALLS.append(model)
    assert "Classic Fries" in prompt and "usually looks like" in prompt
    return VisionResult.model_validate({
        "items_seen": [{"name": "Grilled Chicken Cheese Burger", "qty_estimate": 1, "confidence": 0.95}],
        "items_missing": [{"name": "Classic Fries", "qty_missing": 1, "confidence": 0.97},
                          {"name": "Cold Coffee", "qty_missing": 1, "confidence": 0.95}],
        "is_food_photo": True, "overall_confidence": 0.93, "short_reason": "Burger only."}), "qwen/test-vision", 812


@pytest.fixture()
def client(db, monkeypatch):
    from scripts.seed import seed

    seed(db, do_reset=True, quiet=True)  # API tests run on the real clock, so reseed relative to now
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm, "chat_with_tools", FakeGroq())
    monkeypatch.setattr(llm, "vision_json", fake_vision)
    agent.reset_sessions()
    VISION_CALLS.clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    agent.reset_sessions()


def test_chat_status(client):
    r = client.post("/api/chat", data={"user_id": "priya", "text": "where is my food"})
    assert r.status_code == 200
    d = r.json()
    assert "SW7001" in d["reply"] and d["tools"] == ["get_order_status"] and d["mode"] == "llm"
    assert d["cards"][0]["type"] == "status" and d["cards"][0]["is_delayed"]
    assert "Unofficial" in d["reply"]  # first message carries the disclaimer
    d2 = client.post("/api/chat", data={"user_id": "priya", "text": "where is it now"}).json()
    assert "Unofficial" not in d2["reply"]


def test_chat_photo_complaint_refunds_and_shows_on_dashboard(client, db):
    r = client.post("/api/chat", data={"user_id": "sneha", "text": "my fries are missing", "sample": "burger_no_fries.jpg"})
    d = r.json()
    assert r.status_code == 200 and d["image_url"].startswith("/uploads/img_")
    card = d["cards"][0]
    assert card["type"] == "refund" and card["decision"] == "auto_refund" and card["amount"] == 99
    assert "Classic Fries is missing. ₹99 refunded" in d["reply"]
    c = db.select("complaints", {"user_id": "sneha"})[0]
    assert c["vision_model"] == "qwen/test-vision" and c["vision_latency_ms"] == 812 and c["resolution_seconds"] is not None
    dash = client.get("/api/dashboard").json()
    assert dash["feed"][0]["user"] == "Sneha" and dash["feed"][0]["decision"] == "auto_refund"
    assert dash["tiles"]["complaints_today"] >= 1
    assert dash["tiles"]["vision_calls_saved"] == 0 and dash["feed"][0]["vision_skipped"] is False
    assert len(VISION_CALLS) == 1


def test_chat_same_photo_from_vikram_is_escalated(client):
    client.post("/api/chat", data={"user_id": "sneha", "text": "fries missing", "sample": "burger_no_fries.jpg"})
    with open(SAMPLE, "rb") as f:
        d = client.post("/api/chat", data={"user_id": "vikram", "text": "fries missing"}, files={"image": ("x.jpg", f, "image/jpeg")}).json()
    card = d["cards"][0]
    assert card["decision"] == "escalated" and card["vision_skipped"] is True
    assert "Duplicate photo" in card["flags"] and "High refund rate" in card["flags"]
    assert len(VISION_CALLS) == 1, "Sneha's photo is checked; Vikram's is flagged early and never reaches the vision model"
    assert card["model"] is None and card["latency_ms"] == 0
    dash = client.get("/api/dashboard").json()
    assert dash["tiles"]["vision_calls_saved"] == 1
    vik = next(c for c in dash["escalations"] if c["user_id"] == "vikram")
    assert vik["vision_skipped"] is True and vik["refund"] == 99  # value of the claimed fries, for the human
    assert "Flagged early" in vik["reason"] and "Duplicate photo" in vik["reason"]
    r = client.post(f"/api/complaints/{vik['id']}/decision", json={"approve": True})
    assert r.json()["human_decision"] == "approved" and "₹99 refunded" in r.json()["message"]
    hist = client.get("/api/history", params={"user_id": "vikram"}).json()
    assert hist[-1]["origin"] == "human_agent" and "approved" in hist[-1]["text"]


def test_place_order_needs_cart_shown_first(client):
    d = client.post("/api/chat", data={"user_id": "rahul", "text": "yes"}).json()
    assert "No cart has been shown" in d["reply"]


def test_chat_validation(client):
    assert client.post("/api/chat", data={"user_id": "priya", "text": ""}).status_code == 400
    assert client.post("/api/chat", data={"user_id": "nobody", "text": "hi"}).status_code == 404


def test_demo_controls(client):
    assert client.post("/demo/advance/SW7002").json()["status"] == "preparing"
    assert client.post("/demo/advance/NOPE").status_code == 404
    assert client.post("/demo/reset").json()["ok"]


def test_pages_served(client):
    for path in ["/", "/chat", "/dashboard", "/demo", "/health", "/api/users", "/api/samples"]:
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------- WhatsApp
WA_FORM = {"From": "whatsapp:+919812345678", "To": "whatsapp:+14155238886", "ProfileName": "Tester", "NumMedia": "0"}


def test_whatsapp_text_new_user_gets_demo_order(client, db):
    r = client.post("/whatsapp", data={**WA_FORM, "Body": "where is my food"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    assert "<Response><Message>" in r.text
    user = db.get("users", phone="+919812345678")
    assert user and user["name"] == "Tester"
    orders = db.select("orders", {"user_id": user["id"]})
    assert len(orders) == 1 and orders[0]["status"] == "delivered"
    assert [i["name"] for i in orders[0]["items"]] == ["Classic Fries", "Grilled Chicken Cheese Burger", "Cold Coffee"]
    client.post("/whatsapp", data={**WA_FORM, "Body": "hi"})
    assert db.count("users", {"phone": "+919812345678"}) == 1  # no duplicate user


def test_whatsapp_image_complaint(client, monkeypatch, db):
    from app import whatsapp

    seen = {}

    def fake_download(url):
        seen["url"] = url
        return open(SAMPLE, "rb").read()
    monkeypatch.setattr(whatsapp, "download_media", fake_download)
    r = client.post("/whatsapp", data={**WA_FORM, "Body": "my fries are missing", "NumMedia": "1",
                                       "MediaUrl0": "https://api.twilio.com/media/ME1", "MediaContentType0": "image/jpeg"})
    assert seen["url"] == "https://api.twilio.com/media/ME1"
    assert "₹99 refunded" in r.text
    c = db.select("complaints", {"user_id": "wa_919812345678"})[0]
    assert c["decision"] == "auto_refund"
    conv = db.select("conversations", {"user_id": "wa_919812345678", "role": "user"})
    assert conv[0]["channel"] == "whatsapp"


def test_whatsapp_signature_validation(client, monkeypatch):
    from twilio.request_validator import RequestValidator

    monkeypatch.setattr(settings, "TWILIO_AUTH_TOKEN", "secret-token")
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://demo.example.com")
    form = {**WA_FORM, "Body": "hi"}
    good = RequestValidator("secret-token").compute_signature("https://demo.example.com/whatsapp", form)
    assert client.post("/whatsapp", data=form, headers={"X-Twilio-Signature": good}).status_code == 200
    assert client.post("/whatsapp", data=form, headers={"X-Twilio-Signature": "forged"}).status_code == 403


def test_twiml_escapes_text():
    from app.whatsapp import twiml

    assert "<Message>a &lt;b&gt; &amp; c</Message>" in twiml("a <b> & c")
