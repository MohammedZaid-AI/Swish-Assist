"""Every branch of decide(), plus issue_lines() mapping."""
from app.models import VisionResult
from app.tools.complaints import decide, issue_lines, suggested_reply

ORDER = [
    {"menu_item_id": "fries", "name": "Classic Fries", "qty": 1, "price": 99},
    {"menu_item_id": "chicken_burger", "name": "Grilled Chicken Cheese Burger", "qty": 1, "price": 179},
    {"menu_item_id": "cold_coffee", "name": "Cold Coffee", "qty": 2, "price": 119},
]
FLAG = {"code": "duplicate_photo", "label": "Duplicate photo", "detail": "x"}


def vision(conf=0.92, missing=None, wrong=None, quality=False, seen=None, food=True):
    return VisionResult.model_validate({
        "items_seen": seen or [], "items_missing": missing or [], "wrong_items": wrong or [],
        "quality_issue": {"present": quality, "description": "spilled" if quality else ""},
        "is_food_photo": food, "overall_confidence": conf, "short_reason": "test"})


def run(v, flags=None, value=99, has_issue=True, quality=False, replace=False, dist=0.7):
    return decide(vision=v, fraud_flags=flags or [], refund_value=value, has_issue=has_issue, quality_issue=quality,
                  wants_replacement=replace, kitchen_distance_km=dist, min_confidence=0.8, max_refund_inr=300)


def test_auto_refund():
    d = run(vision())
    assert d.decision == "auto_refund" and d.refund_amount_inr == 99


def test_auto_refund_at_exact_thresholds():
    d = run(vision(conf=0.8), value=300)
    assert d.decision == "auto_refund" and d.refund_amount_inr == 300


def test_auto_replace_when_asked_and_kitchen_close():
    assert run(vision(), replace=True, dist=0.6).decision == "auto_replace"


def test_replacement_falls_back_to_refund_when_kitchen_far():
    assert run(vision(), replace=True, dist=1.1).decision == "auto_refund"


def test_fraud_flag_escalates():
    d = run(vision(), flags=[FLAG])
    assert d.decision == "escalated" and "Duplicate photo" in d.reason


def test_flagged_early_escalates_without_vision():
    d = decide(vision=None, fraud_flags=[FLAG], refund_value=99, has_issue=False, quality_issue=False, wants_replacement=False,
               kitchen_distance_km=0.7, min_confidence=0.8, max_refund_inr=300, vision_skipped=True)
    assert d.decision == "escalated" and d.refund_amount_inr == 99
    assert "Flagged early" in d.reason and "Duplicate photo" in d.reason and "Vision check skipped" in d.reason


def test_fraud_beats_no_issue():
    assert run(vision(), flags=[FLAG], has_issue=False, value=0).decision == "escalated"


def test_low_confidence_escalates():
    d = run(vision(conf=0.79))
    assert d.decision == "escalated" and "below" in d.reason


def test_over_cap_escalates():
    d = run(vision(), value=301)
    assert d.decision == "escalated" and "cap" in d.reason


def test_no_issue_found():
    d = run(vision(conf=0.95), has_issue=False, value=0)
    assert d.decision == "no_issue_found" and d.refund_amount_inr == 0


def test_no_issue_but_low_confidence_escalates():
    assert run(vision(conf=0.5), has_issue=False, value=0).decision == "escalated"


def test_quality_only_escalates():
    d = run(vision(quality=True), has_issue=False, value=0, quality=True)
    assert d.decision == "escalated" and "Quality" in d.reason


def test_vision_failure_escalates():
    assert run(None).decision == "escalated"


# ---- issue_lines -----------------------------------------------------------
def test_issue_lines_prices_missing_and_wrong():
    v = vision(missing=[{"name": "Classic Fries", "qty_missing": 1, "confidence": 0.9},
                        {"name": "cold coffee", "qty_missing": 1, "confidence": 0.85}],
               wrong=[{"expected": "Grilled Chicken Cheese Burger", "seen": "veg burger", "confidence": 0.8}])
    lines, noted = issue_lines(ORDER, v)
    by = {l["name"]: l for l in lines}
    assert by["Classic Fries"]["amount"] == 99
    assert by["Cold Coffee"]["amount"] == 119 and by["Cold Coffee"]["qty"] == 1
    assert by["Grilled Chicken Cheese Burger"]["kind"] == "wrong" and noted == []


def test_issue_lines_only_refunds_claimed_items():
    v = vision(missing=[{"name": "Classic Fries", "confidence": 0.98}, {"name": "Cold Coffee", "qty_missing": 2, "confidence": 0.98}])
    lines, noted = issue_lines(ORDER, v, claimed=["fries"])
    assert [l["name"] for l in lines] == ["Classic Fries"]
    assert [l["name"] for l in noted] == ["Cold Coffee"]


def test_issue_lines_ignores_low_item_confidence():
    v = vision(missing=[{"name": "Classic Fries", "confidence": 0.4}])
    assert issue_lines(ORDER, v)[0] == []


def test_suggested_reply_matches_spec_style():
    lines = [{"name": "Chicken Popcorn", "kind": "missing", "qty": 1, "amount": 129, "confidence": 0.9}]
    assert suggested_reply("auto_refund", lines, 129, None) == \
        "Checked your photo. Your Chicken Popcorn is missing. ₹129 refunded to your Swish wallet. Sorry about that."
