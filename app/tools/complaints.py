"""Photo complaint engine: vision check -> fraud checks -> decision rules -> reply.

check_photo_complaint is the hero tool. decide() holds every decision rule in one pure function.
"""
from __future__ import annotations

import difflib
import json
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path

from .. import events, llm
from ..config import ROOT, settings
from ..db import get_db
from ..models import DecisionResult, VisionResult, iso, parse_ts, utcnow
from . import fraud

log = logging.getLogger("swish.complaints")

SAMPLES_DIR = ROOT / "static" / "samples"
MIN_ITEM_CONFIDENCE = 0.6

VISION_PROMPT = """You check food delivery complaints for Swish, a 10 minute food delivery service in Bengaluru.
The customer ordered these items:
{items}

Customer message: "{message}"

Look carefully at the photo of what arrived. For every ordered item decide if it is visible, missing, or replaced by a clearly different dish.
Rules:
- An item counts as seen if the item or its packaging is visible. Plating, packaging and garnish can differ.
- A different looking version of the same dish is NOT a wrong item. Only use wrong_items when a clearly different dish is there instead.
- Count quantities when you can. If 2 were ordered and 1 is visible, qty_missing is 1.
- Use the item names exactly as written in the order list.
- If the photo does not show food or food packaging, set is_food_photo to false.
- quality_issue is for spilled, burnt, crushed or spoiled food.
- overall_confidence is how sure you are about the whole assessment.

Return strict JSON only, no other text, in exactly this shape:
{{
  "items_seen": [{{"name": "...", "qty_estimate": 1, "confidence": 0.0}}],
  "items_missing": [{{"name": "...", "qty_missing": 1, "confidence": 0.0}}],
  "wrong_items": [{{"expected": "...", "seen": "...", "confidence": 0.0}}],
  "quality_issue": {{"present": false, "description": ""}},
  "is_food_photo": true,
  "overall_confidence": 0.0,
  "short_reason": "one sentence"
}}"""


# ---------------------------------------------------------------- images
def save_image(data: bytes) -> str:
    """Store an uploaded image. Returns an image_id like img_ab12cd34."""
    image_id = "img_" + secrets.token_hex(4)
    d = Path(settings.UPLOAD_DIR)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{image_id}.orig").write_bytes(data)
    try:
        (d / f"{image_id}.jpg").write_bytes(llm.prepare_image(data, max_side=900))
    except Exception:  # noqa: BLE001 - unreadable image: keep the original only
        log.warning("could not decode image %s", image_id)
    return image_id


def load_image(image_id: str) -> bytes | None:
    p = Path(settings.UPLOAD_DIR) / f"{image_id}.orig"
    return p.read_bytes() if p.exists() else None


def image_url(image_id: str) -> str:
    return f"/uploads/{image_id}.jpg"


# ---------------------------------------------------------------- vision
def build_prompt(order_items: list[dict], user_message: str) -> str:
    menu = {m["id"]: m for m in get_db().select("menu_items")}
    lines = []
    for i in order_items:
        look = (menu.get(i.get("menu_item_id")) or {}).get("typical_look") or i.get("typical_look") or ""
        lines.append(f"- {i['qty']}x {i['name']}" + (f" (usually looks like: {look})" if look else ""))
    return VISION_PROMPT.format(items="\n".join(lines), message=(user_message or "").replace('"', "'")[:300])


def mock_vision(order_items: list[dict], image_hash: str) -> VisionResult:
    """Offline mode (no GROQ_API_KEY): recognise the bundled sample photos by perceptual hash."""
    manifest_path = SAMPLES_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for entry in manifest.values():
        if entry.get("hash") and fraud.hamming(image_hash, entry["hash"]) <= fraud.DUPLICATE_MAX_DISTANCE:
            visible = [k.lower() for k in entry["visible"]]
            seen = [i for i in order_items if any(k in i["name"].lower() for k in visible)]
            missing = [i for i in order_items if i not in seen]
            return VisionResult.model_validate({
                "items_seen": [{"name": i["name"], "qty_estimate": i["qty"], "confidence": 0.95} for i in seen],
                "items_missing": [{"name": i["name"], "qty_missing": i["qty"], "confidence": 0.9} for i in missing],
                "is_food_photo": True, "overall_confidence": 0.9 if seen else 0.4,
                "short_reason": entry.get("reason", "Offline check of a known sample photo."),
            })
    return VisionResult(is_food_photo=True, overall_confidence=0.3,
                        short_reason="Offline mode cannot analyse new photos; a human will check it.")


def analyze_photo(order_items: list[dict], image: bytes, user_message: str, model: str | None = None,
                  on_retry=None) -> tuple[VisionResult | None, str, int, str | None]:
    """Returns (vision_result or None, model_used, latency_ms, error)."""
    if not settings.llm_enabled:
        t = time.perf_counter()
        res = mock_vision(order_items, fraud.phash(image))
        return res, "offline-mock", int((time.perf_counter() - t) * 1000), None
    try:
        res, used, ms = llm.vision_json(image, build_prompt(order_items, user_message), VisionResult, model=model, on_retry=on_retry)
        return res, used, ms, None
    except Exception as err:  # noqa: BLE001
        log.exception("vision check failed")
        return None, model or settings.GROQ_VISION_MODEL, 0, str(err)


# ---------------------------------------------------------------- matching and decision
def match_item(name: str, order_items: list[dict]) -> dict | None:
    n = (name or "").lower().strip()
    if not n:
        return None
    for i in order_items:
        iname = i["name"].lower()
        if n == iname or n in iname or iname in n:
            return i
    words = set(n.replace("(", " ").split())
    scored = [(len(words & set(i["name"].lower().replace("(", " ").split())), i) for i in order_items]
    scored = [s for s in scored if s[0] > 0]
    if scored:
        return max(scored, key=lambda s: s[0])[1]
    close = difflib.get_close_matches(n, [i["name"].lower() for i in order_items], n=1, cutoff=0.5)
    return next((i for i in order_items if close and i["name"].lower() == close[0]), None)


def issue_lines(order_items: list[dict], vision: VisionResult, claimed: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Map vision findings to priced order lines. Returns (refundable, noted_only).

    If the customer named specific items, only those items can be refunded; anything else the photo
    can't see is noted for the human but not refunded.
    """
    lines: dict[str, dict] = {}
    for m in vision.items_missing:
        it = match_item(m.name, order_items)
        if it and m.confidence >= MIN_ITEM_CONFIDENCE:
            qty = max(1, min(m.qty_missing, it["qty"]))
            lines[it["name"]] = {"name": it["name"], "kind": "missing", "qty": qty, "amount": it["price"] * qty, "confidence": round(m.confidence, 2)}
    for w in vision.wrong_items:
        it = match_item(w.expected, order_items)
        if it and w.confidence >= MIN_ITEM_CONFIDENCE and it["name"] not in lines:
            lines[it["name"]] = {"name": it["name"], "kind": "wrong", "seen": w.seen, "qty": it["qty"], "amount": it["price"] * it["qty"], "confidence": round(w.confidence, 2)}
    all_lines = list(lines.values())
    claimed_items = [c for c in (match_item(x, order_items) for x in (claimed or [])) if c]
    if not claimed_items:
        return all_lines, []
    names = {c["name"] for c in claimed_items}
    return [l for l in all_lines if l["name"] in names], [l for l in all_lines if l["name"] not in names]


def decide(*, vision: VisionResult | None, fraud_flags: list[dict], refund_value: int, has_issue: bool,
           quality_issue: bool, wants_replacement: bool, kitchen_distance_km: float,
           min_confidence: float, max_refund_inr: float, vision_skipped: bool = False) -> DecisionResult:
    """All decision rules in one pure function."""
    if vision_skipped:
        labels = ", ".join(f["label"] for f in fraud_flags) or "pre-checks"
        return DecisionResult(decision="escalated", refund_amount_inr=refund_value,
                              reason=f"Flagged early by fraud checks: {labels}. Vision check skipped.")
    if vision is None:
        return DecisionResult(decision="escalated", reason="Could not read the photo reliably, a human will check.")
    conf = vision.overall_confidence
    if fraud_flags:
        labels = ", ".join(f["label"] for f in fraud_flags)
        return DecisionResult(decision="escalated", refund_amount_inr=refund_value, reason=f"Fraud checks flagged: {labels}.")
    if conf < min_confidence:
        return DecisionResult(decision="escalated", refund_amount_inr=refund_value,
                              reason=f"Confidence {conf:.2f} is below {min_confidence:.2f}.")
    if not has_issue and not quality_issue:
        return DecisionResult(decision="no_issue_found", reason=f"Everything ordered looks present ({conf:.0%} sure).")
    if not has_issue and quality_issue:
        return DecisionResult(decision="escalated", reason="Quality issue seen, needs a human to judge the refund.")
    if refund_value > max_refund_inr:
        return DecisionResult(decision="escalated", refund_amount_inr=refund_value,
                              reason=f"Refund ₹{refund_value} is above the ₹{int(max_refund_inr)} auto-approve cap.")
    if wants_replacement and kitchen_distance_km < 1:
        return DecisionResult(decision="auto_replace", refund_amount_inr=refund_value,
                              reason=f"Confirmed from photo ({conf:.0%} sure). Kitchen is {kitchen_distance_km} km away, sending a replacement.")
    return DecisionResult(decision="auto_refund", refund_amount_inr=refund_value,
                          reason=f"Confirmed from photo ({conf:.0%} sure), no fraud flags, ₹{refund_value} is within the ₹{int(max_refund_inr)} cap.")


def complaint_type(lines: list[dict], quality: bool) -> str:
    if any(l["kind"] == "missing" for l in lines):
        return "missing_item"
    if any(l["kind"] == "wrong" for l in lines):
        return "wrong_item"
    return "quality" if quality else "other"


def _names(lines: list[dict]) -> str:
    names = [l["name"] + (f" (x{l['qty']})" if l["qty"] > 1 else "") for l in lines]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def suggested_reply(decision: str, lines: list[dict], amount: int, vision: VisionResult | None, eta: int = 10) -> str:
    if decision == "auto_refund":
        verb = "is missing" if len(lines) == 1 and lines[0]["kind"] == "missing" else ("are missing" if all(l["kind"] == "missing" for l in lines) else "was wrong")
        return f"Checked your photo. Your {_names(lines)} {verb}. ₹{amount} refunded to your Swish wallet. Sorry about that."
    if decision == "auto_replace":
        return f"Checked your photo. {_names(lines)} is on its way again from the kitchen, about {eta} minutes. Sorry about that."
    if decision == "no_issue_found":
        seen = ", ".join(s.name for s in (vision.items_seen if vision else [])[:4]) or "your order"
        return f"I checked the photo and can see {seen}. If something is still off, I can connect you to a human."
    return "Thanks, I've passed this to our support team with all the details. They'll get back to you within 15 minutes."


# ---------------------------------------------------------------- tools
def _pick_order(user_id: str, order_id: str | None) -> dict | None:
    db = get_db()
    if order_id:
        o = db.get("orders", id=order_id.strip().upper())
        return o if o and o["user_id"] == user_id else None
    orders = sorted(db.select("orders", {"user_id": user_id}), key=lambda o: o["placed_at"] or "", reverse=True)
    delivered = [o for o in orders if o["status"] == "delivered"]
    return delivered[0] if delivered else (orders[0] if orders else None)


def run_photo_check(*, user_id: str, order: dict, image: bytes, user_message: str = "", claimed_items: list[str] | None = None,
                    wants_replacement: bool = False, image_id: str | None = None, started_at: datetime | None = None,
                    now: datetime | None = None, persist: bool = True, model: str | None = None, on_retry=None) -> dict:
    """Full pipeline. persist=False is used by the eval script."""
    fixed_clock = now is not None
    now = now or utcnow()
    started_at = started_at or now
    image_hash = fraud.phash(image)
    # 1. Cheap checks first (hash, timing, refund history, EXIF). Any flag -> escalate without a vision call.
    flags = fraud.run_cheap_checks(user_id=user_id, order=order, image_hash=image_hash, image_bytes=image, now=now) if persist else []
    skipped = bool(flags)
    if skipped:
        vision, used_model, latency, err = None, None, 0, None
        claimed = [c for c in (match_item(x, order["items"]) for x in (claimed_items or [])) if c]
        claimed = list({c["name"]: c for c in claimed}.values())
        lines, noted = [], []
        value = sum(c["price"] * c["qty"] for c in claimed)
        quality = False
    else:
        # 2. Only now spend the vision call, then run the checks that need its result.
        vision, used_model, latency, err = analyze_photo(order["items"], image, user_message, model=model, on_retry=on_retry)
        flags = fraud.run_content_checks(vision, order) if persist else []
        lines, noted = issue_lines(order["items"], vision, claimed_items) if vision else ([], [])
        value = sum(l["amount"] for l in lines)
        quality = bool(vision and vision.quality_issue.present)
    d = decide(vision=vision, fraud_flags=flags, refund_value=value, has_issue=bool(lines), quality_issue=quality,
               wants_replacement=wants_replacement, kitchen_distance_km=order.get("kitchen_distance_km") or 9,
               min_confidence=settings.AUTO_REFUND_MIN_CONFIDENCE, max_refund_inr=settings.AUTO_REFUND_MAX_INR,
               vision_skipped=skipped)
    if err:
        d.reason = "Vision check failed, escalated to a human."
    decided_at = now if fixed_clock else utcnow()
    resolution = round(max(0.0, (decided_at - started_at).total_seconds()), 1)
    if noted:
        d.reason += f" Also not visible but not reported by customer: {_names(noted)}."
    complaint = {
        "id": "cmp_" + secrets.token_hex(4), "order_id": order["id"], "user_id": user_id,
        "type": ("missing_item" if claimed else "other") if skipped else complaint_type(lines, quality), "user_message": user_message, "image_url": image_url(image_id) if image_id else None,
        "image_hash": image_hash, "vision_model": used_model, "vision_latency_ms": latency,
        "vision_result": vision.model_dump() if vision else (
            {"skipped": True, "reason": "flagged early", "claimed_items": [c["name"] for c in claimed]} if skipped else {"error": err}),
        "fraud_flags": flags,
        "decision": d.decision, "refund_amount_inr": d.refund_amount_inr if d.decision in ("auto_refund", "auto_replace", "escalated") else 0,
        "decision_reason": d.reason + (f" Summary: {_summary(order, lines, vision, flags, user_message, claimed if skipped else None)}" if d.decision == "escalated" else ""),
        "human_decision": None, "created_at": iso(started_at),
        "resolved_at": iso(decided_at) if d.decision != "escalated" else None,
        "resolution_seconds": resolution,
    }
    if persist:
        get_db().insert("complaints", complaint)
        if d.decision == "auto_refund":
            raise_refund(user_id, order["id"], d.refund_amount_inr, complaint["id"])
        events.publish("complaint", complaint_id=complaint["id"], decision=d.decision)
    return {
        "complaint_id": complaint["id"], "order_id": order["id"], "decision": d.decision,
        "refund_amount_inr": complaint["refund_amount_inr"], "reason": d.reason, "issues": lines, "noted_not_refunded": noted,
        "items_seen": [s.name for s in vision.items_seen] if vision else [], "fraud_flags": [f["label"] for f in flags],
        "vision_model": used_model, "vision_latency_ms": latency, "resolution_seconds": resolution, "vision_skipped": skipped,
        "vision": vision.model_dump() if vision else None,
        "suggested_reply": suggested_reply(d.decision, lines, complaint["refund_amount_inr"], vision),
    }


def _summary(order: dict, lines: list[dict], vision: VisionResult | None, flags: list[dict], msg: str,
             skipped_claims: list[dict] | None = None) -> str:
    parts = [f"Order {order['id']} (₹{order['total']}).", f'Customer: "{(msg or "").strip()[:120]}".']
    if skipped_claims is not None:
        claims = ", ".join(f"{c['name']} (₹{c['price'] * c['qty']})" for c in skipped_claims)
        parts.append((f"Customer reports: {claims}. " if claims else "") + "Photo not checked by the vision model (flagged early).")
    if lines:
        parts.append("Photo shows " + "; ".join(f"{l['name']} {l['kind']} ({l['confidence']:.0%})" for l in lines) + ".")
    if vision:
        parts.append(f"Vision: {vision.short_reason} (confidence {vision.overall_confidence:.2f}).")
    if flags:
        parts.append("Flags: " + "; ".join(f["detail"] for f in flags))
    return " ".join(parts)


def check_photo_complaint(user_id: str, order_id: str | None, image_id: str | None, user_message: str = "",
                          claimed_items: list[str] | None = None, wants_replacement: bool = False,
                          started_at: datetime | None = None, now: datetime | None = None, on_retry=None) -> dict:
    order = _pick_order(user_id, order_id)
    if not order:
        return {"error": "Could not find that order on your account."}
    image = load_image(image_id) if image_id else None
    if image is None:
        return {"error": "No photo found. Ask the customer to send a photo of what arrived."}
    res = run_photo_check(user_id=user_id, order=order, image=image, user_message=user_message, claimed_items=claimed_items,
                          wants_replacement=wants_replacement, image_id=image_id, started_at=started_at, now=now, on_retry=on_retry)
    res["_card"] = {"type": "refund", "decision": res["decision"], "amount": res["refund_amount_inr"], "order_id": order["id"],
                    "items": [{"name": l["name"], "kind": l["kind"], "confidence": l["confidence"]} for l in res["issues"]],
                    "flags": res["fraud_flags"], "reason": res["reason"], "model": res["vision_model"],
                    "latency_ms": res["vision_latency_ms"], "seconds": res["resolution_seconds"], "vision_skipped": res["vision_skipped"]}
    res.pop("vision", None)
    return res


def raise_refund(user_id: str, order_id: str, amount: int, complaint_id: str) -> dict:
    """Credit the refund. In this demo the wallet credit is the complaint record itself; a real build calls the payments API."""
    log.info("refund ₹%s to %s for %s (%s)", amount, user_id, order_id, complaint_id)
    return {"refunded": amount, "to": "swish_wallet", "complaint_id": complaint_id}


def escalate_to_human(user_id: str, order_id: str | None, summary: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    order = _pick_order(user_id, order_id)
    cid = "cmp_" + secrets.token_hex(4)
    get_db().insert("complaints", {
        "id": cid, "order_id": order["id"] if order else order_id, "user_id": user_id, "type": "other",
        "user_message": summary[:300], "decision": "escalated", "refund_amount_inr": 0,
        "decision_reason": f"Customer asked for a human. Summary: {summary}", "fraud_flags": [], "created_at": iso(now),
    })
    events.publish("complaint", complaint_id=cid, decision="escalated")
    return {"complaint_id": cid, "status": "escalated", "message": "A support agent will reply within 15 minutes."}


def human_decide(complaint_id: str, approve: bool, now: datetime | None = None) -> dict | None:
    """Dashboard Approve / Reject. Returns the complaint plus the message to send to the user."""
    now = now or utcnow()
    db = get_db()
    c = db.get("complaints", id=complaint_id)
    if not c:
        return None
    amount = c.get("refund_amount_inr") or 0
    if approve and not amount:
        order = db.get("orders", id=c["order_id"])
        vr = c.get("vision_result") or {}
        missing = vr.get("items_missing") or [{"name": n} for n in vr.get("claimed_items") or []]
        if order:
            amount = sum((match_item(m["name"], order["items"]) or {}).get("price", 0) for m in missing) or 0
    created = parse_ts(c["created_at"]) or now
    values = {"human_decision": "approved" if approve else "rejected", "resolved_at": iso(now),
              "resolution_seconds": round((now - created).total_seconds(), 1), "refund_amount_inr": amount if approve else 0}
    if not approve:
        values["decision"] = "rejected"
    db.update("complaints", {"id": complaint_id}, values)
    if approve and amount:
        raise_refund(c["user_id"], c["order_id"], amount, complaint_id)
    msg = (f"Update on order {c['order_id']}: our support team approved your complaint. ₹{amount} refunded to your Swish wallet."
           if approve and amount else
           f"Update on order {c['order_id']}: our support team reviewed your complaint and resolved it." if approve else
           f"Update on order {c['order_id']}: our team reviewed your photo and couldn't confirm the issue, so no refund this time. Reply here if you want to talk to someone.")
    events.publish("complaint", complaint_id=complaint_id, decision=values.get("decision", c["decision"]))
    return {"complaint": db.get("complaints", id=complaint_id), "message": msg}

