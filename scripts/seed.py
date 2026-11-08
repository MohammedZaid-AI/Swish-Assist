"""Seed the demo database: menu, 6 demo users, ~80 past orders, live orders, coupons, complaints.

Usage:
    python scripts/seed.py           # safe to run again, never duplicates
    python scripts/seed.py --reset   # wipe everything and reseed
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import Database, get_db  # noqa: E402
from app.models import IST, iso, utcnow  # noqa: E402

PH = "https://placehold.co/400x300/0f9d58/ffffff?text="

# id, name, category, price, veg, description, typical_look
MENU = [
    ("fries", "Classic Fries", "sides", 99, True, "Salted crispy fries", "golden fries standing upright in a red paper fries box"),
    ("peri_fries", "Peri Peri Fries", "sides", 119, True, "Fries tossed in peri peri", "orange-dusted fries in a red paper fries box"),
    ("loaded_fries", "Loaded Cheese Fries", "sides", 169, True, "Fries, cheese sauce, jalapenos", "fries covered in yellow cheese sauce and green jalapenos in a black bowl"),
    ("chicken_popcorn", "Chicken Popcorn", "chicken", 129, False, "Bite-size crispy chicken", "small golden fried chicken bites in a white paper tub"),
    ("chicken_wings", "Peri Peri Chicken Wings", "chicken", 249, False, "6 glazed wings", "six red-brown glazed wings in a black takeaway box"),
    ("veg_burger", "Crispy Veg Burger", "burgers", 149, True, "Aloo-veg patty, lettuce, mayo", "sesame bun burger in yellow wrap paper inside a brown kraft burger box"),
    ("chicken_burger", "Grilled Chicken Cheese Burger", "burgers", 179, False, "Grilled chicken patty, cheese, lettuce, tomato", "tall sesame bun burger with a dark grilled patty, melted cheese slice, lettuce and tomato"),
    ("paneer_wrap", "Paneer Tikka Wrap", "wraps", 139, True, "Tandoori paneer, onions, mint mayo", "rolled tortilla wrap half wrapped in green paper"),
    ("chicken_wrap", "Chicken Tikka Wrap", "wraps", 159, False, "Chicken tikka, onions, mint mayo", "rolled tortilla wrap half wrapped in red paper"),
    ("masala_maggi", "Masala Maggi", "maggi", 89, True, "Classic masala noodles", "yellow noodles in a round kraft paper bowl with a lid"),
    ("cheese_maggi", "Cheese Maggi", "maggi", 109, True, "Masala noodles with cheese", "yellow noodles with melted cheese in a round kraft paper bowl"),
    ("masala_chai", "Masala Chai", "beverages", 49, True, "Kadak ginger-cardamom chai", "small brown paper cup with a white sip lid"),
    ("filter_coffee", "Filter Coffee", "beverages", 59, True, "Bengaluru style filter coffee", "small white paper cup of milky brown coffee with a lid"),
    ("cold_coffee", "Cold Coffee", "beverages", 119, True, "Thick blended cold coffee", "tall clear plastic cup of light brown coffee with a dome lid and a black straw"),
    ("iced_tea", "Lemon Iced Tea", "beverages", 99, True, "Lemon iced tea", "tall clear plastic cup of amber tea with ice and a lemon slice"),
    ("idli", "Idli (2 pcs)", "south_indian", 69, True, "Steamed idli, chutney, sambar", "two round white idlis in a foil container with small chutney and sambar cups"),
    ("medu_vada", "Medu Vada (2 pcs)", "south_indian", 79, True, "Crispy vada, chutney, sambar", "two brown doughnut-shaped vadas in a foil container with chutney cups"),
    ("masala_dosa", "Masala Dosa", "south_indian", 119, True, "Crispy dosa, potato masala", "long folded golden dosa in a rectangular box with a sambar cup"),
    ("pav_bhaji", "Pav Bhaji", "mains", 139, True, "Buttery bhaji with 2 pav", "red-orange bhaji in a round container with two buttered buns in foil"),
    ("paneer_bowl", "Paneer Rice Bowl", "bowls", 189, True, "Paneer makhani over jeera rice", "red-orange paneer curry over white rice in a black rectangular bowl"),
    ("chicken_bowl", "Chicken Rice Bowl", "bowls", 229, False, "Butter chicken over jeera rice", "orange butter chicken over white rice in a black rectangular bowl"),
    ("veg_momos", "Veg Momos (6 pcs)", "momos", 109, True, "Steamed veg momos, red chutney", "six white steamed dumplings in a round foil box with red chutney"),
    ("chicken_momos", "Chicken Momos (6 pcs)", "momos", 129, False, "Steamed chicken momos, red chutney", "six white pleated dumplings in a round foil box with red chutney"),
    ("brownie", "Walnut Brownie", "desserts", 89, True, "Fudgy walnut brownie", "dark square chocolate brownie in a small kraft box"),
    ("cookies", "Choco Chip Cookies (3)", "desserts", 79, True, "Fresh baked cookies", "three round chocolate chip cookies in a clear pouch"),
]
MENU_BY_ID = {m[0]: m for m in MENU}

USERS = [
    # id, phone, name, language, kitchen, distance
    ("priya", "+919000000001", "Priya", "en", "Swish HSR Sector 2", 0.8),
    ("rahul", "+919000000002", "Rahul", "en", "Swish HSR Sector 6", 0.9),
    ("ananya", "+919000000003", "Ananya", "kn", "Swish HSR Sector 4", 0.6),
    ("arjun", "+919000000004", "Arjun", "hi_en", "Swish HSR Sector 1", 1.1),
    ("sneha", "+919000000005", "Sneha", "en", "Swish HSR Sector 2", 0.7),
    ("vikram", "+919000000006", "Vikram", "en", "Swish HSR Sector 2", 0.9),
]
USER_BY_ID = {u[0]: u for u in USERS}
RIDERS = ["Ravi", "Manjunath", "Imran", "Suresh", "Kiran", "Deepak"]
DELAY_REASON = "heavy rain near HSR Sector 2, rider rerouted"


def coupons(now: datetime) -> list[dict]:
    year_ago, in_a_year = iso(now - timedelta(days=365)), iso(now + timedelta(days=365))
    base = dict(valid_from=year_ago, valid_to=in_a_year, first_order_only=False, max_uses_per_user=None,
                allowed_days=None, allowed_hours=None, active=True, max_discount_inr=None, min_order_inr=0)
    return [
        {**base, "code": "SWISH50", "description": "Flat ₹50 off on orders above ₹299", "type": "flat", "value": 50, "min_order_inr": 299},
        {**base, "code": "HUNGRY20", "description": "20% off up to ₹80 on orders above ₹199", "type": "percent", "value": 20, "min_order_inr": 199, "max_discount_inr": 80},
        {**base, "code": "FIRSTBITE", "description": "Flat ₹100 off on your first order above ₹149", "type": "flat", "value": 100, "min_order_inr": 149, "first_order_only": True},
        {**base, "code": "MONSOON30", "description": "30% off up to ₹100 (monsoon special)", "type": "percent", "value": 30, "max_discount_inr": 100,
         "valid_from": iso(now - timedelta(days=120)), "valid_to": iso(now - timedelta(days=10))},
        {**base, "code": "LUNCH40", "description": "Flat ₹40 off on weekday lunch (12-3 pm) above ₹149", "type": "flat", "value": 40, "min_order_inr": 149,
         "allowed_days": [0, 1, 2, 3, 4], "allowed_hours": [12, 15]},
        {**base, "code": "TWICE25", "description": "25% off up to ₹60, usable twice per user", "type": "percent", "value": 25, "min_order_inr": 99,
         "max_discount_inr": 60, "max_uses_per_user": 2},
    ]


def _line(mid: str, qty: int = 1) -> dict:
    m = MENU_BY_ID[mid]
    return {"menu_item_id": mid, "name": m[1], "qty": qty, "price": m[3]}


def make_order(oid: str, user_id: str, items: list[tuple[str, int]], placed: datetime, rng: random.Random,
               status: str = "delivered", coupon: tuple[str, int] | None = None, delay_reason: str | None = None,
               eta: int = 10) -> dict:
    u = USER_BY_ID.get(user_id)
    lines = [_line(i, q) for i, q in items]
    subtotal = sum(l["price"] * l["qty"] for l in lines)
    discount = coupon[1] if coupon else 0
    stages = {"preparing_at": 1, "packed_at": 5, "out_for_delivery_at": 6, "delivered_at": rng.choice([9, 10, 10, 11, 12])}
    order = {
        "id": oid, "user_id": user_id, "items": lines, "subtotal": subtotal, "discount": discount,
        "total": subtotal - discount, "coupon_code": coupon[0] if coupon else None, "status": status,
        "placed_at": iso(placed), "eta_minutes": eta, "delay_reason": delay_reason,
        "kitchen_name": u[4] if u else "Swish HSR Sector 2", "kitchen_distance_km": u[5] if u else 0.8,
        "rider_name": rng.choice(RIDERS),
        "preparing_at": None, "packed_at": None, "out_for_delivery_at": None, "delivered_at": None,
    }
    order_of = ["placed", "preparing", "packed", "out_for_delivery", "delivered"]
    reached = order_of.index(status) if status in order_of else 4
    for i, (col, minutes) in enumerate(stages.items(), start=1):
        if i <= reached:
            order[col] = iso(placed + timedelta(minutes=minutes, seconds=rng.randint(0, 40)))
    return order


def _at(now_ist: datetime, days_ago: int, hour: float) -> datetime:
    d = (now_ist - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d + timedelta(hours=hour)


def build_orders(now: datetime) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (orders, complaints, coupon_uses). Deterministic for a given 'now'."""
    rng = random.Random(42)
    now_ist = now.astimezone(IST)
    orders: list[dict] = []
    seq = iter(range(1001, 9999))

    def add(user, items, when, **kw):
        if when >= now - timedelta(minutes=20):
            return None
        o = make_order(f"SW{next(seq)}", user, items, when, rng, **kw)
        orders.append(o)
        return o

    for days_ago in range(45, 0, -1):
        day = (now_ist - timedelta(days=days_ago))
        weekday = day.weekday() < 5
        # Priya: cold coffee + chicken popcorn, weekday evenings
        if weekday and rng.random() < 0.62:
            items = [("cold_coffee", 1), ("chicken_popcorn", 1)]
            if rng.random() < 0.15:
                items.append(("brownie", 1))
            add("priya", items, _at(now_ist, days_ago, rng.uniform(17.5, 20)))
        elif not weekday and rng.random() < 0.35:
            add("priya", [("peri_fries", 1), ("cold_coffee", 1)], _at(now_ist, days_ago, rng.uniform(15, 17)))
        # Rahul: paneer bowl + masala chai, weekday lunch
        if weekday and rng.random() < 0.62:
            o = add("rahul", [("paneer_bowl", 1), ("masala_chai", 1)], _at(now_ist, days_ago, rng.uniform(12.75, 13.75)),
                    coupon=("LUNCH40", 40) if rng.random() < 0.3 else None)
        # Ananya: south Indian breakfast
        if rng.random() < 0.22:
            items = rng.choice([[("idli", 1), ("filter_coffee", 1)], [("masala_dosa", 1), ("filter_coffee", 1)], [("idli", 1), ("medu_vada", 1)]])
            add("ananya", items, _at(now_ist, days_ago, rng.uniform(8, 10)))
        # Arjun: late night maggi / momos
        if rng.random() < 0.27:
            items = rng.choice([[("masala_maggi", 1), ("chicken_momos", 1)], [("loaded_fries", 1), ("iced_tea", 1)], [("cheese_maggi", 2)]])
            add("arjun", items, _at(now_ist, days_ago, rng.uniform(22, 23.8)))
        # Sneha: occasional mixed orders
        if days_ago % 6 == 3:
            items = rng.choice([[("chicken_wrap", 1), ("iced_tea", 1)], [("pav_bhaji", 1)], [("fries", 1), ("chicken_burger", 1)]])
            add("sneha", items, _at(now_ist, days_ago, rng.uniform(13, 21)))

    # Vikram: 8 orders; refunds asked on 5 of them
    vik_days = [40, 34, 29, 23, 18, 12, 6]
    vik_orders = [add("vikram", [("fries", 1), ("chicken_burger", 1), ("cold_coffee", 1)], _at(now_ist, d, rng.uniform(13, 21))) for d in vik_days]
    complaints: list[dict] = []
    for i, o in enumerate(vik_orders[:5]):
        refunded = o["items"][i % 3]
        delivered = datetime.fromisoformat(o["delivered_at"])
        human = i >= 3
        complaints.append({
            "id": f"cmp_seed_vik{i}", "order_id": o["id"], "user_id": "vikram", "type": "missing_item",
            "user_message": f"{refunded['name']} was missing", "image_url": None,
            "image_hash": f"{rng.getrandbits(64):016x}", "vision_model": "qwen/qwen3.8-27b",
            "vision_latency_ms": rng.randint(1500, 2600),
            "vision_result": {"items_missing": [{"name": refunded["name"], "qty_missing": 1, "confidence": 0.82}], "overall_confidence": 0.82, "short_reason": "Item not visible in photo"},
            "fraud_flags": [], "decision": "escalated" if human else "auto_refund",
            "refund_amount_inr": refunded["price"], "decision_reason": "Missing item confirmed from photo",
            "human_decision": "approved" if human else None,
            "created_at": iso(delivered + timedelta(minutes=8)), "resolved_at": iso(delivered + timedelta(minutes=9)),
            "resolution_seconds": float(rng.randint(20, 40)) if not human else float(rng.randint(600, 1800)),
        })

    # Today's background activity, so the dashboard is not empty on first open
    def today_order(user, items, minutes_ago):
        return add(user, items, now - timedelta(minutes=minutes_ago))

    r = today_order("rahul", [("paneer_bowl", 1), ("masala_chai", 1)], 190)
    a = today_order("ananya", [("idli", 1), ("filter_coffee", 1)], 150)
    j = today_order("arjun", [("masala_maggi", 1), ("chicken_momos", 1)], 75)
    for cid, o, typ, msg, vr, decision, amount, reason, secs, model, lat in [
        ("cmp_seed_today1", r, "missing_item", "chai nahi aayi", {"items_seen": [{"name": "Paneer Rice Bowl", "qty_estimate": 1, "confidence": 0.95}], "items_missing": [{"name": "Masala Chai", "qty_missing": 1, "confidence": 0.93}], "wrong_items": [], "overall_confidence": 0.93, "short_reason": "Rice bowl visible, no chai cup in the bag."},
         "auto_refund", 49, "Masala Chai missing (93% sure), no fraud flags, ₹49 is under the ₹300 cap.", 24.0, "qwen/qwen3.8-27b", 1720),
        ("cmp_seed_today2", a, "wrong_item", "got vada instead of idli", {"items_seen": [{"name": "Medu Vada", "qty_estimate": 2, "confidence": 0.9}], "items_missing": [], "wrong_items": [{"expected": "Idli (2 pcs)", "seen": "Medu Vada", "confidence": 0.9}], "overall_confidence": 0.9, "short_reason": "Vadas visible where idlis were expected."},
         "auto_replace", 69, "Wrong item: Medu Vada sent instead of Idli. Kitchen is 0.6 km away, replacement dispatched.", 31.0, "qwen/qwen3.8-27b", 1980),
        ("cmp_seed_today3", j, "quality", "maggi spilled all over the bag", {"items_seen": [{"name": "Masala Maggi", "qty_estimate": 1, "confidence": 0.7}], "items_missing": [], "wrong_items": [], "quality_issue": {"present": True, "description": "Noodle bowl lid open, spill visible"}, "overall_confidence": 0.7, "short_reason": "Spill visible but unclear how much is lost."},
         "escalated", 0, "Quality issue with no fixed refund value and confidence 0.70 is below 0.80.", None, "qwen/qwen3.8-27b", 2210),
    ]:
        delivered = datetime.fromisoformat(o["delivered_at"])
        complaints.append({
            "id": cid, "order_id": o["id"], "user_id": o["user_id"], "type": typ, "user_message": msg,
            "image_url": None, "image_hash": f"{rng.getrandbits(64):016x}", "vision_model": model, "vision_latency_ms": lat,
            "vision_result": vr, "fraud_flags": [], "decision": decision, "refund_amount_inr": amount,
            "decision_reason": reason, "human_decision": None,
            "created_at": iso(delivered + timedelta(minutes=4)),
            "resolved_at": iso(delivered + timedelta(minutes=4, seconds=secs)) if secs else None,
            "resolution_seconds": secs,
        })

    # The two demo-critical delivered orders
    orders.append(make_order("SW5005", "sneha", [("fries", 1), ("chicken_burger", 1), ("cold_coffee", 1)],
                             now - timedelta(minutes=20), rng))
    orders[-1]["delivered_at"] = iso(now - timedelta(minutes=10))
    orders.append(make_order("SW5006", "vikram", [("fries", 1), ("chicken_burger", 1), ("cold_coffee", 1)],
                             now - timedelta(minutes=25), rng))
    orders[-1]["delivered_at"] = iso(now - timedelta(minutes=14))

    # Live orders in different stages
    orders.append(make_order("SW7001", "priya", [("cold_coffee", 1), ("chicken_popcorn", 1)], now - timedelta(minutes=12), rng,
                             status="out_for_delivery", delay_reason=DELAY_REASON, eta=18, coupon=("HUNGRY20", 49)))
    orders.append(make_order("SW7002", "rahul", [("paneer_bowl", 1), ("masala_chai", 1)], now - timedelta(seconds=20), rng, status="placed"))
    orders.append(make_order("SW7003", "arjun", [("masala_maggi", 1), ("chicken_momos", 1)], now - timedelta(minutes=2, seconds=30), rng, status="preparing"))

    priya_past = [o for o in orders if o["user_id"] == "priya" and o["status"] == "delivered"]
    uses = []
    for n, o in enumerate(priya_past[2:4], start=1):  # Priya already used TWICE25 twice
        o["coupon_code"], o["discount"] = "TWICE25", min(60, o["subtotal"] // 4)
        o["total"] = o["subtotal"] - o["discount"]
    for o in orders:
        if o["coupon_code"]:
            uses.append({"id": f"use_{o['id']}", "user_id": o["user_id"], "coupon_code": o["coupon_code"], "order_id": o["id"], "used_at": o["placed_at"]})
    return orders, complaints, uses


def reset(db: Database) -> None:
    for t in ["conversations", "complaints", "coupon_uses", "orders", "coupons", "users", "menu_items"]:
        db.delete(t)


def _upsert(db: Database, table: str, pk: str, rows: list[dict]) -> int:
    existing = {r[pk] for r in db.select(table)}
    n = 0
    for r in rows:
        if r[pk] not in existing:
            db.insert(table, r)
            n += 1
    return n


def seed(db: Database | None = None, do_reset: bool = False, now: datetime | None = None, quiet: bool = False) -> dict:
    db = db or get_db()
    now = now or utcnow()
    if do_reset:
        reset(db)
    added = {}
    added["menu_items"] = _upsert(db, "menu_items", "id", [
        {"id": m[0], "name": m[1], "category": m[2], "price_inr": m[3], "is_veg": m[4], "description": m[5],
         "image_url": PH + m[1].replace(" ", "+"), "typical_look": m[6]} for m in MENU])
    added["users"] = _upsert(db, "users", "id", [
        {"id": u[0], "phone": u[1], "name": u[2], "language_pref": u[3], "created_at": iso(now - timedelta(days=90))} for u in USERS])
    added["coupons"] = _upsert(db, "coupons", "code", coupons(now))
    demo_ids = [u[0] for u in USERS]
    if db.count("orders", {"user_id": demo_ids}) == 0:
        orders, complaints, uses = build_orders(now)
        added["orders"] = _upsert(db, "orders", "id", orders)
        added["complaints"] = _upsert(db, "complaints", "id", complaints)
        added["coupon_uses"] = _upsert(db, "coupon_uses", "id", uses)
    totals = {t: db.count(t) for t in ["menu_items", "users", "coupons", "orders", "complaints", "coupon_uses"]}
    if not quiet:
        print("added:", added)
        print("totals:", totals)
    return totals


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reset", action="store_true", help="wipe all tables and reseed")
    args = ap.parse_args()
    seed(do_reset=args.reset)
