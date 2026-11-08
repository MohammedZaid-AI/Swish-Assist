"""Moves live orders through their stages with realistic 10-minute timings.

placed -> preparing (~1 min) -> packed (~5) -> out_for_delivery (~6) -> delivered (~10)
Timings scale with the order's eta_minutes. An order with a delay_reason never auto-delivers:
the simulator keeps nudging its ETA forward so the delay explanation can be demoed.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from . import events
from .db import get_db
from .models import LIVE_STATUSES, STAGES, iso, parse_ts, utcnow

log = logging.getLogger("swish.sim")

# minute marks (for a 10 minute ETA) at which each stage starts
STAGE_AT = {"preparing": 1.0, "packed": 5.0, "out_for_delivery": 6.0, "delivered": 10.0}


def target_stage(order: dict, now: datetime) -> str:
    elapsed = (now - parse_ts(order["placed_at"])).total_seconds() / 60
    scale = (order.get("eta_minutes") or 10) / 10 if not order.get("delay_reason") else 1.0
    stage = "placed"
    for st, mark in STAGE_AT.items():
        if elapsed >= mark * scale:
            stage = st
    if order.get("delay_reason") and stage == "delivered":
        stage = "out_for_delivery"
    return stage


def tick(now: datetime | None = None) -> list[str]:
    """One simulation step. Returns ids of orders that changed."""
    now = now or utcnow()
    db = get_db()
    changed = []
    for o in db.select("orders", {"status": LIVE_STATUSES}):
        cur = STAGES.index(o["status"])
        tgt = STAGES.index(target_stage(o, now))
        values: dict = {}
        if tgt > cur:
            for st in STAGES[cur + 1: tgt + 1]:
                values[f"{st}_at"] = iso(now)
            values["status"] = STAGES[tgt]
        if o.get("delay_reason"):
            elapsed = (now - parse_ts(o["placed_at"])).total_seconds() / 60
            if elapsed > o["eta_minutes"] - 2:  # still stuck in rain: honest, updated ETA
                values["eta_minutes"] = int(elapsed) + 6
        if values:
            db.update("orders", {"id": o["id"]}, values)
            changed.append(o["id"])
            if "status" in values:
                events.publish("order", order_id=o["id"], status=values["status"])
    return changed


def advance(order_id: str, now: datetime | None = None) -> dict | None:
    """Jump an order to its next stage (demo control)."""
    now = now or utcnow()
    db = get_db()
    o = db.get("orders", id=order_id)
    if not o:
        return None
    if o["status"] not in LIVE_STATUSES:
        return o
    nxt = STAGES[STAGES.index(o["status"]) + 1]
    values = {"status": nxt, f"{nxt}_at": iso(now)}
    if nxt == "delivered":
        values["delay_reason"] = None
    db.update("orders", {"id": order_id}, values)
    events.publish("order", order_id=order_id, status=nxt)
    return db.get("orders", id=order_id)


# Seeded demo orders that must always look recent, so the demo works hours after a deploy.
# order id -> minutes since delivery (delivered orders) or since placement (the rain-delayed live order)
DEMO_DELIVERED = {"SW5005": 10, "SW5006": 14}
DEMO_DELAYED = {"SW7001": 12}
TS_COLS = ["placed_at", "preparing_at", "packed_at", "out_for_delivery_at", "delivered_at"]


def _shift(order: dict, delta) -> dict:
    return {c: iso(parse_ts(order[c]) + delta) for c in TS_COLS if order.get(c)}


def keep_demo_fresh(now: datetime | None = None) -> list[str]:
    """Re-anchor untouched seeded demo orders to 'now'. Orders with a complaint, or that moved on, are left alone."""
    now = now or utcnow()
    db = get_db()
    changed = []
    for oid, minutes in DEMO_DELIVERED.items():
        o = db.get("orders", id=oid)
        if not o or o["status"] != "delivered" or not o.get("delivered_at"):
            continue
        if now - parse_ts(o["delivered_at"]) < timedelta(hours=2) or db.count("complaints", {"order_id": oid}):
            continue
        db.update("orders", {"id": oid}, _shift(o, (now - timedelta(minutes=minutes)) - parse_ts(o["delivered_at"])))
        changed.append(oid)
    for oid, minutes in DEMO_DELAYED.items():
        o = db.get("orders", id=oid)
        if not o or o["status"] != "out_for_delivery" or not o.get("delay_reason"):
            continue
        if now - parse_ts(o["placed_at"]) < timedelta(minutes=40):
            continue
        values = _shift(o, (now - timedelta(minutes=minutes)) - parse_ts(o["placed_at"]))
        values["eta_minutes"] = minutes + 6
        db.update("orders", {"id": oid}, values)
        changed.append(oid)
    return changed


async def run(interval: float = 3.0) -> None:
    log.info("order simulator started")
    ticks = 0
    while True:
        try:
            if ticks % 20 == 0:
                await asyncio.to_thread(keep_demo_fresh)
            ticks += 1
            await asyncio.to_thread(tick)
        except Exception:  # never let the simulator die
            log.exception("simulator tick failed")
        await asyncio.sleep(interval)
