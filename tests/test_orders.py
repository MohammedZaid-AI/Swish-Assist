from datetime import datetime, timedelta, timezone

from app import simulator
from app.tools.orders import get_order_status, get_recent_orders, get_usual_order, time_bucket
from app.tools.reorder import place_order

NOW = datetime(2026, 11, 4, 13, 30, tzinfo=timezone.utc)  # Wed 19:00 IST


def test_usual_priya_weekday_evening(db):
    res = get_usual_order("priya", "usual", NOW)
    ids = sorted(i["menu_item_id"] for i in res["items"])
    assert ids == ["chicken_popcorn", "cold_coffee"]
    assert res["times_ordered"] >= 10
    assert "evening" in res["why_picked"] or "dinner" in res["why_picked"] or "frequent" in res["why_picked"]


def test_usual_rahul_lunch_hint(db):
    res = get_usual_order("rahul", "my lunch", NOW)
    assert sorted(i["menu_item_id"] for i in res["items"]) == ["masala_chai", "paneer_bowl"]
    assert "lunch" in res["why_picked"]


def test_usual_last_weekday(db):
    res = get_usual_order("priya", "same as last friday", NOW)
    assert "Friday" in res["why_picked"]
    o = db.get("orders", id=res["from_order_id"])
    from app.models import parse_ts, to_ist
    assert to_ist(parse_ts(o["placed_at"])).weekday() == 4


def test_usual_last_time(db):
    res = get_usual_order("sneha", "same as last time", NOW)
    assert res["from_order_id"] == "SW5005"


def test_usual_no_orders(db):
    db.insert("users", {"id": "empty", "phone": "+91x", "name": "E", "language_pref": "en"})
    assert "error" in get_usual_order("empty", "usual", NOW)


def test_time_buckets():
    assert time_bucket(8) == "breakfast" and time_bucket(13) == "lunch" and time_bucket(17) == "evening"
    assert time_bucket(21) == "dinner" and time_bucket(1) == "late_night"


def test_status_delayed_live_order(db):
    res = get_order_status("priya", now=NOW)
    assert res["order_id"] == "SW7001" and res["stage"] == "out_for_delivery"
    assert res["is_delayed"] and "rain" in res["delay_reason"]
    assert res["eta_minutes_remaining"] >= 1 and res["rider"]


def test_status_specific_and_foreign(db):
    assert get_order_status("sneha", "SW5005", NOW)["stage"] == "delivered"
    assert "error" in get_order_status("priya", "SW5005", NOW)


def test_recent_orders(db):
    res = get_recent_orders("sneha", 3)
    assert res["count"] == 3 and res["orders"][0]["order_id"] == "SW5005"


def test_simulator_moves_order_through_stages(db):
    res = place_order("priya", [{"menu_item_id": "fries", "qty": 1}], None, NOW)
    oid = res["order_id"]
    seen = []
    for minute in [0.5, 1.5, 5.5, 6.5, 10.5]:
        simulator.tick(NOW + timedelta(minutes=minute))
        seen.append(db.get("orders", id=oid)["status"])
    assert seen == ["placed", "preparing", "packed", "out_for_delivery", "delivered"]
    o = db.get("orders", id=oid)
    assert all(o[f"{s}_at"] for s in ["preparing", "packed", "out_for_delivery", "delivered"])


def test_simulator_keeps_delayed_order(db):
    simulator.tick(NOW + timedelta(minutes=30))
    o = db.get("orders", id="SW7001")
    assert o["status"] == "out_for_delivery" and o["eta_minutes"] > 18


def test_keep_demo_fresh(db):
    later = NOW + timedelta(hours=5)
    db.insert("complaints", {"id": "c_x", "order_id": "SW5006", "user_id": "vikram", "decision": "escalated"})
    changed = simulator.keep_demo_fresh(later)
    assert "SW5005" in changed and "SW7001" in changed and "SW5006" not in changed  # SW5006 already has a complaint
    from app.models import parse_ts
    o = db.get("orders", id="SW5005")
    assert abs((later - parse_ts(o["delivered_at"])).total_seconds() - 600) < 2
    assert parse_ts(o["placed_at"]) < parse_ts(o["delivered_at"])
    live = get_order_status("priya", "SW7001", later)
    assert live["is_delayed"] and 1 <= live["eta_minutes_remaining"] <= 7
    assert simulator.keep_demo_fresh(later) == []  # idempotent


def test_advance(db):
    o = simulator.advance("SW7002", NOW)
    assert o["status"] == "preparing" and o["preparing_at"]
    for _ in range(5):
        simulator.advance("SW7002", NOW)
    assert db.get("orders", id="SW7002")["status"] == "delivered"
    assert simulator.advance("nope", NOW) is None
