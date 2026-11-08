"""Order tools: get_order_status, get_recent_orders, get_usual_order."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

from ..db import get_db
from ..models import IST, LIVE_STATUSES, STAGES, parse_ts, to_ist, utcnow

STAGE_LABELS = {
    "placed": "Order placed",
    "preparing": "Being prepared in the kitchen",
    "packed": "Packed, waiting for rider",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ALIASES = {d[:3]: i for i, d in enumerate(DAYS)} | {d: i for i, d in enumerate(DAYS)}


def user_orders(user_id: str) -> list[dict]:
    rows = get_db().select("orders", {"user_id": user_id})
    return sorted(rows, key=lambda o: o["placed_at"] or "", reverse=True)


def items_text(items: list[dict]) -> str:
    return ", ".join(f"{i['qty']}x {i['name']}" if i.get("qty", 1) > 1 else i["name"] for i in items)


def order_summary(o: dict) -> dict:
    return {
        "order_id": o["id"], "status": o["status"], "placed_at_ist": to_ist(parse_ts(o["placed_at"])).strftime("%a %d %b, %I:%M %p"),
        "items": [{"name": i["name"], "qty": i["qty"], "price": i["price"]} for i in o["items"]],
        "subtotal": o["subtotal"], "discount": o["discount"], "total": o["total"], "coupon_code": o["coupon_code"],
    }


# ---------------------------------------------------------------- status
def status_payload(o: dict, now: datetime | None = None) -> dict:
    now = now or utcnow()
    placed = parse_ts(o["placed_at"])
    elapsed = (now - placed).total_seconds() / 60
    live = o["status"] in LIVE_STATUSES
    remaining = max(1, round(o["eta_minutes"] - elapsed)) if live else 0
    timeline = []
    for st in STAGES:
        ts = parse_ts(o.get(f"{st}_at")) if st != "placed" else placed
        timeline.append({"stage": st, "label": STAGE_LABELS[st], "at_ist": to_ist(ts).strftime("%I:%M %p") if ts else None, "done": ts is not None})
    out = {
        "order_id": o["id"], "stage": o["status"], "stage_label": STAGE_LABELS.get(o["status"], o["status"]),
        "is_live": live, "minutes_since_placed": round(elapsed, 1),
        "eta_minutes_remaining": remaining, "promised_eta_minutes": o["eta_minutes"],
        "delay_reason": o.get("delay_reason") if live else None,
        "is_delayed": bool(o.get("delay_reason")) and live,
        "kitchen": o["kitchen_name"], "kitchen_distance_km": o["kitchen_distance_km"],
        "rider": o["rider_name"] if o["status"] in ("out_for_delivery", "delivered") else None,
        "items": items_text(o["items"]), "total": o["total"], "timeline": timeline,
    }
    if o["status"] == "delivered" and o.get("delivered_at"):
        out["delivered_minutes_ago"] = round((now - parse_ts(o["delivered_at"])).total_seconds() / 60)
    return out


def get_order_status(user_id: str, order_id: str | None = None, now: datetime | None = None) -> dict:
    db = get_db()
    if order_id:
        o = db.get("orders", id=order_id.strip().upper())
        if not o or o["user_id"] != user_id:
            return {"error": f"No order {order_id} found on your account."}
    else:
        orders = user_orders(user_id)
        if not orders:
            return {"error": "You have no orders yet."}
        live = [x for x in orders if x["status"] in LIVE_STATUSES]
        o = live[0] if live else orders[0]
    payload = status_payload(o, now)
    payload["_card"] = {"type": "status", **payload}
    return payload


def get_recent_orders(user_id: str, limit: int = 5) -> dict:
    limit = max(1, min(int(limit or 5), 10))
    orders = user_orders(user_id)[:limit]
    return {"orders": [order_summary(o) for o in orders], "count": len(orders)}


# ---------------------------------------------------------------- usual order
def time_bucket(hour: float) -> str:
    if 6 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 15.5:
        return "lunch"
    if 15.5 <= hour < 19:
        return "evening"
    if 19 <= hour < 23:
        return "dinner"
    return "late_night"


BUCKET_WORDS = {
    "breakfast": "breakfast", "morning": "breakfast", "nashta": "breakfast", "lunch": "lunch", "afternoon": "lunch",
    "evening": "evening", "snack": "evening", "shaam": "evening", "dinner": "dinner", "night": "dinner",
    "late": "late_night", "midnight": "late_night",
}


def _signature(items: list[dict]) -> tuple:
    return tuple(sorted((i["menu_item_id"], i["qty"]) for i in items))


def _reply_items(items: list[dict]) -> list[dict]:
    return [{"menu_item_id": i["menu_item_id"], "name": i["name"], "qty": i["qty"], "price": i["price"]} for i in items]


def get_usual_order(user_id: str, hint: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    now_ist = to_ist(now)
    hint_l = (hint or "usual").lower()
    past = [o for o in user_orders(user_id) if o["status"] == "delivered"]
    if not past:
        return {"error": "No past orders to repeat yet."}

    # 1) explicit day: "last friday", "same as friday"
    day_idx = next((DAY_ALIASES[w] for w in re.findall(r"[a-z]+", hint_l) if w in DAY_ALIASES), None)
    if "yesterday" in hint_l:
        target = (now_ist.date().toordinal() - 1)
        match = [o for o in past if to_ist(parse_ts(o["placed_at"])).date().toordinal() == target]
        if match:
            return _pick(match[0], f"Your order from yesterday ({to_ist(parse_ts(match[0]['placed_at'])).strftime('%a %d %b')}).", 1)
        return {"error": "No order found from yesterday."}
    if day_idx is not None:
        match = [o for o in past if to_ist(parse_ts(o["placed_at"])).weekday() == day_idx
                 and to_ist(parse_ts(o["placed_at"])).date() < now_ist.date()]
        if match:
            when = to_ist(parse_ts(match[0]["placed_at"])).strftime("%a %d %b")
            return _pick(match[0], f"Your most recent {DAYS[day_idx].title()} order ({when}).", 1)
        return {"error": f"No past order found on a {DAYS[day_idx].title()}."}

    # 2) "last time" / "last order"
    if re.search(r"\blast (time|order)\b|\bprevious\b|\bpichl", hint_l):
        return _pick(past[0], "Your most recent order.", 1)

    # 3) frequency x day-type x time-of-day scoring
    want_bucket = next((b for w, b in BUCKET_WORDS.items() if w in hint_l), None) or time_bucket(now_ist.hour + now_ist.minute / 60)
    want_weekday = now_ist.weekday() < 5
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for o in past:
        groups[_signature(o["items"])].append(o)
    best, best_score, best_detail = None, -1.0, {}
    for sig, orders in groups.items():
        score, same_bucket, same_daytype = 0.0, 0, 0
        for o in orders:
            t = to_ist(parse_ts(o["placed_at"]))
            days_ago = (now - parse_ts(o["placed_at"])).days
            w = 1.0
            if time_bucket(t.hour + t.minute / 60) == want_bucket:
                w += 2.0
                same_bucket += 1
            if (t.weekday() < 5) == want_weekday:
                w += 1.0
                same_daytype += 1
            score += w * (0.985 ** days_ago)
        if score > best_score:
            best, best_score = orders, score
            best_detail = {"times": len(orders), "same_bucket": same_bucket, "same_daytype": same_daytype}
    n = best_detail["times"]
    bucket_word = want_bucket.replace("_", " ")
    if best_detail["same_bucket"] >= max(1, n // 2):
        why = f"You've ordered this {n} times, mostly around {bucket_word}" + (" on weekdays." if want_weekday and best_detail["same_daytype"] >= n // 2 else ".")
    else:
        why = f"Your most frequent order ({n} times in the last 45 days)."
    return _pick(best[0], why, n)


def _pick(order: dict, why: str, times: int) -> dict:
    return {
        "items": _reply_items(order["items"]), "items_text": items_text(order["items"]),
        "from_order_id": order["id"], "times_ordered": times, "why_picked": why,
        "subtotal": sum(i["price"] * i["qty"] for i in order["items"]),
    }
