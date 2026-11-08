"""Fraud checks for photo complaints. Each check is a small pure function; run_fraud_checks wires them to the DB.

Flags are dicts: {"code": "...", "label": "...", "detail": "..."}
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta

import imagehash
from PIL import Image

from ..db import get_db
from ..models import IST, VisionResult, parse_ts

DUPLICATE_MAX_DISTANCE = 8          # phash hamming distance (64-bit) counted as "same photo"
LATE_COMPLAINT_HOURS = 3
REFUND_RATE_LIMIT = 0.30
REFUNDED = ("auto_refund", "auto_replace")


def flag(code: str, label: str, detail: str) -> dict:
    return {"code": code, "label": label, "detail": detail}


# ---------------------------------------------------------------- 1. duplicate photo
def phash(image_bytes: bytes) -> str:
    return str(imagehash.phash(Image.open(io.BytesIO(image_bytes)).convert("RGB")))


def hamming(a: str, b: str) -> int:
    try:
        return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)
    except (ValueError, TypeError):
        return 64


def check_duplicate(image_hash: str, past: list[dict]) -> dict | None:
    """past: [{"id", "image_hash", "user_id", "order_id"}] of earlier complaints."""
    best = None
    for p in past:
        if not p.get("image_hash"):
            continue
        d = hamming(image_hash, p["image_hash"])
        if d <= DUPLICATE_MAX_DISTANCE and (best is None or d < best[0]):
            best = (d, p)
    if best:
        d, p = best
        who = "another customer" if p.get("user_id") and p.get("user_id") != p.get("_current_user") else "this customer"
        return flag("duplicate_photo", "Duplicate photo",
                    f"Near-identical to the photo in complaint {p['id']} on order {p.get('order_id')} by {who} (distance {d}).")
    return None


# ---------------------------------------------------------------- 2. timing
def check_timing(now: datetime, status: str, delivered_at: datetime | None) -> dict | None:
    if status != "delivered" or delivered_at is None:
        return flag("before_delivery", "Before delivery", f"Order is still '{status}', not delivered yet.")
    hours = (now - delivered_at).total_seconds() / 3600
    if hours > LATE_COMPLAINT_HOURS:
        return flag("late_complaint", "Late complaint", f"Raised {hours:.1f} h after delivery (limit {LATE_COMPLAINT_HOURS} h).")
    return None


# ---------------------------------------------------------------- 3. refund history
def refund_rate(order_ids_last10: list[str], complaints: list[dict]) -> tuple[float, int]:
    if not order_ids_last10:
        return 0.0, 0
    refunded = {c["order_id"] for c in complaints
                if c["order_id"] in order_ids_last10 and (c.get("decision") in REFUNDED or c.get("human_decision") == "approved")}
    return len(refunded) / len(order_ids_last10), len(refunded)


def check_refund_history(rate: float, refunded: int, total: int) -> dict | None:
    if rate > REFUND_RATE_LIMIT:
        return flag("high_refund_rate", "High refund rate", f"Refunds on {refunded} of last {total} orders ({rate:.0%}).")
    return None


# ---------------------------------------------------------------- 4. photo content
def check_photo_content(vision: VisionResult | None, order_item_names: list[str]) -> dict | None:
    if vision is None:
        return None
    if not vision.is_food_photo:
        return flag("not_food", "Not a food photo", "The photo does not show food or food packaging.")
    flagged = {m.name.lower() for m in vision.items_missing} | {w.expected.lower() for w in vision.wrong_items}
    names = [n.lower() for n in order_item_names]
    if names and len(order_item_names) > 1 and all(any(n in f or f in n for f in flagged) for n in names) and not vision.items_seen:
        return flag("photo_mismatch", "Photo does not match order", "None of the ordered items are visible in the photo.")
    return None


# ---------------------------------------------------------------- 5. EXIF date
def exif_datetime(image_bytes: bytes) -> datetime | None:
    try:
        exif = Image.open(io.BytesIO(image_bytes)).getexif()
    except Exception:  # noqa: BLE001
        return None
    raw = None
    try:
        raw = exif.get_ifd(0x8769).get(36867)  # DateTimeOriginal
    except Exception:  # noqa: BLE001
        pass
    raw = raw or exif.get(306)  # DateTime
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None


def check_exif(taken_at: datetime | None, placed_at: datetime) -> dict | None:
    if taken_at and taken_at < placed_at - timedelta(minutes=5):
        return flag("old_photo", "Old photo (EXIF)", f"Photo taken {taken_at.strftime('%d %b %H:%M')}, before the order was placed.")
    return None


# ---------------------------------------------------------------- runners
def run_cheap_checks(*, user_id: str, order: dict, image_hash: str | None, image_bytes: bytes | None,
                     now: datetime) -> list[dict]:
    """Checks that need no vision call: duplicate photo hash, timing, refund history, EXIF date.

    They run first; if any flags, the photo complaint escalates without spending a vision call.
    """
    db = get_db()
    flags: list[dict] = []
    if image_hash:
        past = [dict(c, _current_user=user_id) for c in db.select("complaints")
                if c.get("image_hash") and not (c["order_id"] == order["id"] and c["user_id"] == user_id)]
        flags.append(check_duplicate(image_hash, past))
    flags.append(check_timing(now, order["status"], parse_ts(order.get("delivered_at"))))
    orders = sorted(db.select("orders", {"user_id": user_id}), key=lambda o: o["placed_at"] or "", reverse=True)
    last10 = [o["id"] for o in orders if o["status"] == "delivered"][:10]
    rate, n = refund_rate(last10, db.select("complaints", {"user_id": user_id}))
    flags.append(check_refund_history(rate, n, len(last10)))
    if image_bytes:
        flags.append(check_exif(exif_datetime(image_bytes), parse_ts(order["placed_at"])))
    return [f for f in flags if f]


def run_content_checks(vision: VisionResult | None, order: dict) -> list[dict]:
    """Checks that need the vision result (not a food photo, photo does not match the order)."""
    f = check_photo_content(vision, [i["name"] for i in order["items"]])
    return [f] if f else []


def run_fraud_checks(*, user_id: str, order: dict, image_hash: str | None, image_bytes: bytes | None,
                     vision: VisionResult | None, now: datetime) -> list[dict]:
    """All checks at once (cheap + content)."""
    return (run_cheap_checks(user_id=user_id, order=order, image_hash=image_hash, image_bytes=image_bytes, now=now)
            + run_content_checks(vision, order))
