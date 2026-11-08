"""Reorder tools: build_cart, apply_best_coupon (coupon engine), place_order, search_menu."""
from __future__ import annotations

import difflib
import secrets
from datetime import datetime

from .. import events
from ..db import get_db
from ..models import iso, parse_ts, to_ist, utcnow
from .orders import status_payload

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
RIDERS = ["Ravi", "Manjunath", "Imran", "Suresh", "Kiran", "Deepak"]


# ---------------------------------------------------------------- menu & cart
def menu() -> list[dict]:
    return get_db().select("menu_items", order_by="name")


def resolve_item(ref: dict, items: list[dict]) -> dict | None:
    by_id = {m["id"]: m for m in items}
    mid = (ref.get("menu_item_id") or "").strip()
    if mid in by_id:
        return by_id[mid]
    name = (ref.get("name") or mid or "").lower().strip()
    if not name:
        return None
    names = {m["name"].lower(): m for m in items}
    for n, m in names.items():
        if name == n or name in n:
            return m
    close = difflib.get_close_matches(name, list(names), n=1, cutoff=0.55)
    return names[close[0]] if close else None


def build_cart(cart_items: list[dict]) -> dict:
    """cart_items: [{menu_item_id or name, qty}] -> priced cart with current menu prices."""
    items = menu()
    lines, unknown = [], []
    for ref in cart_items or []:
        m = resolve_item(ref, items)
        qty = max(1, min(int(ref.get("qty") or 1), 20))
        if not m:
            unknown.append(ref.get("name") or ref.get("menu_item_id"))
            continue
        existing = next((l for l in lines if l["menu_item_id"] == m["id"]), None)
        if existing:
            existing["qty"] += qty
        else:
            lines.append({"menu_item_id": m["id"], "name": m["name"], "qty": qty, "price": m["price_inr"]})
    return {"lines": lines, "subtotal": sum(l["price"] * l["qty"] for l in lines), "unknown_items": unknown}


def search_menu(query: str = "") -> dict:
    q = (query or "").lower()
    items = menu()
    hits = [m for m in items if not q or q in m["name"].lower() or q in (m["category"] or "") or q in (m["description"] or "").lower()]
    if not hits:
        names = difflib.get_close_matches(q, [m["name"].lower() for m in items], n=5, cutoff=0.4)
        hits = [m for m in items if m["name"].lower() in names]
    return {"items": [{"menu_item_id": m["id"], "name": m["name"], "price": m["price_inr"], "veg": m["is_veg"]} for m in hits[:12]]}


# ---------------------------------------------------------------- coupon engine
def evaluate_coupon(coupon: dict, subtotal: int, now: datetime, uses_by_user: int, user_order_count: int) -> tuple[bool, int, str]:
    """Pure rule check. Returns (applies, discount_inr, reason)."""
    code = coupon["code"]
    if not coupon.get("active", True):
        return False, 0, f"{code} is not active."
    vf, vt = parse_ts(coupon.get("valid_from")), parse_ts(coupon.get("valid_to"))
    if vf and now < vf:
        return False, 0, f"{code} starts on {to_ist(vf).strftime('%d %b')}."
    if vt and now > vt:
        return False, 0, f"{code} expired on {to_ist(vt).strftime('%d %b')}."
    if coupon.get("first_order_only") and user_order_count > 0:
        return False, 0, f"{code} is only for a first order."
    max_uses = coupon.get("max_uses_per_user")
    if max_uses is not None and uses_by_user >= max_uses:
        return False, 0, f"{code} already used {uses_by_user} of {max_uses} times."
    local = to_ist(now)
    days = coupon.get("allowed_days")
    if days is not None and local.weekday() not in days:
        allowed = ", ".join(DAY_NAMES[d] for d in days)
        return False, 0, f"{code} works only on {allowed}."
    hours = coupon.get("allowed_hours")
    if hours:
        start, end = hours
        if not (start <= local.hour < end):
            return False, 0, f"{code} works only between {_hr(start)} and {_hr(end)}."
    min_order = coupon.get("min_order_inr") or 0
    if subtotal < min_order:
        return False, 0, f"{code} needs a ₹{min_order} order, add ₹{min_order - subtotal} more."
    if coupon["type"] == "flat":
        discount = coupon["value"]
    else:
        discount = subtotal * coupon["value"] // 100
    cap = coupon.get("max_discount_inr")
    if cap is not None:
        discount = min(discount, cap)
    discount = max(0, min(discount, subtotal))
    return True, int(discount), f"{code}: {coupon['description']}"


def _hr(h: int) -> str:
    h = int(h) % 24
    return f"{12 if h % 12 == 0 else h % 12} {'am' if h < 12 else 'pm'}"


def _user_coupon_context(user_id: str) -> tuple[dict[str, int], int]:
    db = get_db()
    uses: dict[str, int] = {}
    for u in db.select("coupon_uses", {"user_id": user_id}):
        uses[u["coupon_code"]] = uses.get(u["coupon_code"], 0) + 1
    orders = [o for o in db.select("orders", {"user_id": user_id}) if o["status"] != "cancelled"]
    return uses, len(orders)


def best_coupon_for(user_id: str, subtotal: int, now: datetime | None = None) -> dict:
    now = now or utcnow()
    uses, n_orders = _user_coupon_context(user_id)
    applicable, rejected = [], []
    for c in get_db().select("coupons"):
        ok, disc, why = evaluate_coupon(c, subtotal, now, uses.get(c["code"], 0), n_orders)
        (applicable if ok else rejected).append({"code": c["code"], "saving": disc, "reason": why})
    applicable.sort(key=lambda x: -x["saving"])
    best = applicable[0] if applicable and applicable[0]["saving"] > 0 else None
    others = [{"code": a["code"], "reason": f"saves only ₹{a['saving']}"} for a in applicable[1:]] + [
        {"code": r["code"], "reason": r["reason"]} for r in rejected]
    return {"best": best, "not_applied": others}


def apply_best_coupon(user_id: str, cart: list[dict], now: datetime | None = None) -> dict:
    built = build_cart(cart)
    if not built["lines"]:
        return {"error": "Cart is empty or items not found on the menu.", "unknown_items": built["unknown_items"]}
    res = best_coupon_for(user_id, built["subtotal"], now)
    saving = res["best"]["saving"] if res["best"] else 0
    out = {
        "cart": built["lines"], "subtotal": built["subtotal"],
        "best_coupon": res["best"]["code"] if res["best"] else None,
        "best_coupon_reason": res["best"]["reason"] if res["best"] else None,
        "saving": saving, "total": built["subtotal"] - saving,
        "coupons_not_applied": res["not_applied"], "unknown_items": built["unknown_items"],
        "next_step": "Show this cart, coupon and total to the user and ask them to confirm with yes.",
    }
    out["_card"] = {"type": "cart", "lines": built["lines"], "subtotal": built["subtotal"], "coupon": out["best_coupon"],
                    "coupon_reason": out["best_coupon_reason"], "saving": saving, "total": out["total"],
                    "not_applied": res["not_applied"][:4]}
    return out


# ---------------------------------------------------------------- place order
def _kitchen_for(user_id: str) -> tuple[str, float]:
    db = get_db()
    past = sorted(db.select("orders", {"user_id": user_id}), key=lambda o: o["placed_at"] or "", reverse=True)
    if past:
        return past[0]["kitchen_name"], past[0]["kitchen_distance_km"]
    return "Swish HSR Sector 2", 0.8


def place_order(user_id: str, cart: list[dict], coupon_code: str | None = None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    db = get_db()
    built = build_cart(cart)
    if not built["lines"]:
        return {"error": "Cart is empty or items not found on the menu."}
    discount, note = 0, None
    if coupon_code:
        c = db.get("coupons", code=coupon_code.strip().upper())
        if not c:
            note = f"Coupon {coupon_code} not found, placed without it."
            coupon_code = None
        else:
            uses, n = _user_coupon_context(user_id)
            ok, discount, why = evaluate_coupon(c, built["subtotal"], now, uses.get(c["code"], 0), n)
            if not ok:
                note, coupon_code, discount = f"{why} Placed without a coupon.", None, 0
            else:
                coupon_code = c["code"]
    kitchen, dist = _kitchen_for(user_id)
    eta = 10 if dist < 1 else 12
    oid = "SW" + secrets.token_hex(3).upper()
    order = {
        "id": oid, "user_id": user_id, "items": built["lines"], "subtotal": built["subtotal"], "discount": discount,
        "total": built["subtotal"] - discount, "coupon_code": coupon_code, "status": "placed", "placed_at": iso(now),
        "eta_minutes": eta, "delay_reason": None, "kitchen_name": kitchen, "kitchen_distance_km": dist,
        "rider_name": RIDERS[int(now.timestamp()) % len(RIDERS)],
    }
    db.insert("orders", order)
    if coupon_code:
        db.insert("coupon_uses", {"id": "use_" + oid, "user_id": user_id, "coupon_code": coupon_code, "order_id": oid, "used_at": iso(now)})
    events.publish("order", order_id=oid, status="placed")
    status = status_payload(order, now)
    return {"order_id": oid, "status": "placed", "total": order["total"], "discount": discount, "coupon_code": coupon_code,
            "eta_minutes": eta, "note": note, "items": built["lines"], "_card": {"type": "status", **status}}
