from datetime import datetime, timedelta, timezone

from app.models import iso
from app.tools.reorder import apply_best_coupon, evaluate_coupon, place_order

NOW = datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc)  # Wed 19:00 IST
WED_LUNCH = datetime(2026, 11, 4, 7, 30, tzinfo=timezone.utc)  # Wed 13:00 IST
SAT_LUNCH = datetime(2026, 11, 7, 7, 30, tzinfo=timezone.utc)  # Sat 13:00 IST


def coupon(**kw):
    base = dict(code="TEST", description="test", type="flat", value=50, min_order_inr=0, max_discount_inr=None,
                valid_from=iso(NOW - timedelta(days=30)), valid_to=iso(NOW + timedelta(days=30)), first_order_only=False,
                max_uses_per_user=None, allowed_days=None, allowed_hours=None, active=True)
    base.update(kw)
    return base


def test_flat_applies():
    assert evaluate_coupon(coupon(), 300, NOW, 0, 5)[:2] == (True, 50)


def test_flat_never_exceeds_subtotal():
    assert evaluate_coupon(coupon(value=500), 120, NOW, 0, 5)[:2] == (True, 120)


def test_percent_with_cap():
    ok, disc, _ = evaluate_coupon(coupon(type="percent", value=20, max_discount_inr=80), 1000, NOW, 0, 5)
    assert ok and disc == 80
    ok, disc, _ = evaluate_coupon(coupon(type="percent", value=20, max_discount_inr=80), 248, NOW, 0, 5)
    assert ok and disc == 49


def test_expired():
    ok, _, why = evaluate_coupon(coupon(valid_to=iso(NOW - timedelta(days=1))), 500, NOW, 0, 5)
    assert not ok and "expired" in why


def test_not_started():
    ok, _, why = evaluate_coupon(coupon(valid_from=iso(NOW + timedelta(days=1))), 500, NOW, 0, 5)
    assert not ok and "starts" in why


def test_inactive():
    assert not evaluate_coupon(coupon(active=False), 500, NOW, 0, 5)[0]


def test_min_order():
    ok, _, why = evaluate_coupon(coupon(min_order_inr=299), 248, NOW, 0, 5)
    assert not ok and "add ₹51 more" in why
    assert evaluate_coupon(coupon(min_order_inr=299), 299, NOW, 0, 5)[0]


def test_first_order_only():
    assert evaluate_coupon(coupon(first_order_only=True), 300, NOW, 0, 0)[0]
    ok, _, why = evaluate_coupon(coupon(first_order_only=True), 300, NOW, 0, 3)
    assert not ok and "first order" in why


def test_per_user_limit():
    assert evaluate_coupon(coupon(max_uses_per_user=2), 300, NOW, 1, 5)[0]
    ok, _, why = evaluate_coupon(coupon(max_uses_per_user=2), 300, NOW, 2, 5)
    assert not ok and "2 of 2" in why


def test_allowed_days_and_hours():
    lunch = coupon(allowed_days=[0, 1, 2, 3, 4], allowed_hours=[12, 15])
    assert evaluate_coupon(lunch, 300, WED_LUNCH, 0, 5)[0]
    ok, _, why = evaluate_coupon(lunch, 300, SAT_LUNCH, 0, 5)
    assert not ok and "Mon" in why
    ok, _, why = evaluate_coupon(lunch, 300, NOW, 0, 5)
    assert not ok and "12 pm and 3 pm" in why


def test_best_coupon_for_priya_evening(db):
    res = apply_best_coupon("priya", [{"menu_item_id": "cold_coffee", "qty": 1}, {"menu_item_id": "chicken_popcorn", "qty": 1}], NOW)
    assert res["subtotal"] == 248
    assert res["best_coupon"] == "HUNGRY20" and res["saving"] == 49 and res["total"] == 199
    reasons = {c["code"]: c["reason"] for c in res["coupons_not_applied"]}
    assert "expired" in reasons["MONSOON30"]
    assert "2 of 2" in reasons["TWICE25"]
    assert "first order" in reasons["FIRSTBITE"]
    assert "add ₹51" in reasons["SWISH50"]
    assert res["_card"]["type"] == "cart"


def test_best_coupon_weekday_lunch_rahul(db):
    res = apply_best_coupon("rahul", [{"menu_item_id": "masala_chai", "qty": 1}, {"menu_item_id": "veg_burger", "qty": 1}], WED_LUNCH)
    # subtotal 198: HUNGRY20 needs 199, LUNCH40 (flat 40) beats TWICE25 (25% = 49)? no: TWICE25 gives 49
    assert res["subtotal"] == 198
    assert res["best_coupon"] == "TWICE25" and res["saving"] == 49
    assert any(c["code"] == "LUNCH40" and "saves only ₹40" in c["reason"] for c in res["coupons_not_applied"])


def test_new_user_gets_first_order_coupon(db):
    db.insert("users", {"id": "newbie", "phone": "+910", "name": "New", "language_pref": "en"})
    res = apply_best_coupon("newbie", [{"name": "cold coffee", "qty": 2}], NOW)
    assert res["best_coupon"] == "FIRSTBITE" and res["saving"] == 100


def test_place_order_records_coupon_use(db):
    res = place_order("priya", [{"menu_item_id": "cold_coffee", "qty": 1}, {"menu_item_id": "chicken_popcorn", "qty": 1}], "HUNGRY20", NOW)
    assert res["total"] == 199 and res["status"] == "placed"
    assert db.get("orders", id=res["order_id"])["status"] == "placed"
    assert any(u["order_id"] == res["order_id"] for u in db.select("coupon_uses", {"user_id": "priya"}))


def test_place_order_drops_invalid_coupon(db):
    res = place_order("priya", [{"menu_item_id": "cold_coffee", "qty": 1}], "MONSOON30", NOW)
    assert res["coupon_code"] is None and res["total"] == 119 and "expired" in res["note"]
