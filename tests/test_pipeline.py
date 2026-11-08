"""Photo complaint pipeline order: cheap fraud checks first, vision only when they pass."""
from datetime import timedelta

import pytest

from app.models import VisionResult
from app.tools import complaints, fraud

SAMPLE = open("static/samples/burger_no_fries.jpg", "rb").read()


@pytest.fixture()
def vision_calls(monkeypatch):
    calls = []

    def fake(order_items, image, user_message, model=None, on_retry=None):
        calls.append(user_message)
        v = VisionResult.model_validate({
            "items_seen": [{"name": "Grilled Chicken Cheese Burger", "confidence": 0.95}],
            "items_missing": [{"name": "Classic Fries", "qty_missing": 1, "confidence": 0.95}],
            "is_food_photo": calls[-1] != "not food", "overall_confidence": 0.92, "short_reason": "Burger only."})
        return v, "qwen/test", 700, None
    monkeypatch.setattr(complaints, "analyze_photo", fake)
    return calls


def _check(db, user, order_id, now, msg="my fries are missing"):
    order = db.get("orders", id=order_id)
    return complaints.run_photo_check(user_id=user, order=order, image=SAMPLE, user_message=msg,
                                      claimed_items=["fries"], now=now)


def test_clean_case_calls_vision_and_refunds(db, now, vision_calls):
    res = _check(db, "sneha", "SW5005", now)
    assert len(vision_calls) == 1 and res["decision"] == "auto_refund" and res["vision_skipped"] is False


def test_late_complaint_skips_vision(db, now, vision_calls):
    res = _check(db, "sneha", "SW5005", now + timedelta(hours=5))
    assert vision_calls == [] and res["vision_skipped"] is True
    assert res["decision"] == "escalated" and res["fraud_flags"] == ["Late complaint"]
    assert res["vision_model"] is None and res["vision_latency_ms"] == 0 and res["refund_amount_inr"] == 99
    c = db.get("complaints", id=res["complaint_id"])
    assert c["vision_result"]["skipped"] is True and c["vision_result"]["claimed_items"] == ["Classic Fries"]
    assert "Vision check skipped" in c["decision_reason"] and "Late complaint" in c["decision_reason"]
    assert "h after delivery" in c["decision_reason"]


def test_duplicate_and_refund_history_skip_vision(db, now, vision_calls):
    _check(db, "sneha", "SW5005", now)
    res = _check(db, "vikram", "SW5006", now)
    assert len(vision_calls) == 1  # only Sneha's
    assert res["vision_skipped"] and {"Duplicate photo", "High refund rate"} <= set(res["fraud_flags"])


def test_content_checks_still_run_after_vision(db, now, vision_calls):
    res = _check(db, "sneha", "SW5005", now, msg="not food")
    assert len(vision_calls) == 1 and res["vision_skipped"] is False
    assert res["decision"] == "escalated" and res["fraud_flags"] == ["Not a food photo"]


def test_cheap_checks_never_need_vision(db, now):
    order = db.get("orders", id="SW5005")
    assert fraud.run_cheap_checks(user_id="sneha", order=order, image_hash=fraud.phash(SAMPLE), image_bytes=SAMPLE, now=now) == []


def test_human_approve_of_skipped_case_refunds_claimed_items(db, now, vision_calls):
    res = _check(db, "sneha", "SW5005", now + timedelta(hours=5))
    out = complaints.human_decide(res["complaint_id"], approve=True, now=now + timedelta(hours=5, minutes=2))
    assert out["complaint"]["refund_amount_inr"] == 99 and "₹99 refunded" in out["message"]
