"""FastAPI app: pages, chat API, dashboard API + SSE, demo controls, WhatsApp webhook."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, events, simulator, whatsapp
from .config import ROOT, settings
from .db import get_db
from .models import LIVE_STATUSES, parse_ts, to_ist, utcnow
from .tools import complaints
from .tools.orders import items_text, status_payload

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("swish")

STATIC = ROOT / "static"
DEMO_USERS = ["priya", "rahul", "ananya", "arjun", "sneha", "vikram"]
USER_BLURBS = {
    "priya": "Cold coffee + chicken popcorn, weekday evenings. Has a live order delayed by rain.",
    "rahul": "Paneer rice bowl + chai, weekday lunch. Has a live order just placed.",
    "ananya": "Prefers Kannada replies.",
    "arjun": "Chats in Hinglish, late-night maggi.",
    "sneha": "Order delivered 10 min ago: fries, burger, cold coffee.",
    "vikram": "Suspicious: refunds on 5 of last 8 orders.",
}
MANUAL_RESOLUTION_SECONDS = 25 * 60


@asynccontextmanager
async def lifespan(app: FastAPI):
    events.bind_loop(asyncio.get_running_loop())
    db = get_db()
    db.create_tables()
    if db.count("users") == 0:
        from scripts.seed import seed

        log.info("Empty database, seeding demo data")
        await asyncio.to_thread(seed, db)
    Path(settings.UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(simulator.run()) if settings.SIMULATOR_ENABLED else None
    log.info("Swish Assist ready. DB=%s, LLM=%s", db.backend, settings.GROQ_CHAT_MODEL if settings.llm_enabled else "offline")
    yield
    if task:
        task.cancel()


app = FastAPI(title="Swish Assist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")


# ---------------------------------------------------------------- pages
def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers={"Cache-Control": "no-cache"})


@app.get("/", include_in_schema=False)
def index():
    return _page("index.html")


@app.get("/chat", include_in_schema=False)
def chat_page():
    return _page("chat.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return _page("dashboard.html")


@app.get("/demo", include_in_schema=False)
def demo_page():
    return _page("demo.html")


@app.get("/health")
def health():
    return {"ok": True, "db": get_db().backend, "llm": settings.llm_enabled}


# ---------------------------------------------------------------- chat API
@app.get("/api/config")
def api_config():
    return {"llm_enabled": settings.llm_enabled, "chat_model": settings.GROQ_CHAT_MODEL, "vision_model": settings.GROQ_VISION_MODEL,
            "db": get_db().backend, "auto_refund_max_inr": settings.AUTO_REFUND_MAX_INR,
            "auto_refund_min_confidence": settings.AUTO_REFUND_MIN_CONFIDENCE, "github_url": settings.GITHUB_URL}


@app.get("/api/users")
def api_users():
    db = get_db()
    users = {u["id"]: u for u in db.select("users", {"id": DEMO_USERS})}
    return [{"id": uid, "name": users[uid]["name"], "language_pref": users[uid]["language_pref"], "blurb": USER_BLURBS[uid]}
            for uid in DEMO_USERS if uid in users]


@app.get("/api/samples")
def api_samples():
    manifest = json.loads((STATIC / "samples" / "manifest.json").read_text(encoding="utf-8"))
    return [{"file": f, "url": f"/static/samples/{f}", "label": m.get("label", f)} for f, m in manifest.items()]


@app.post("/api/chat")
def api_chat(user_id: str = Form(...), text: str = Form(""), image: UploadFile | None = File(None), sample: str = Form("")):
    data = None
    if image is not None and image.filename:
        data = image.file.read()
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(413, "Image too large (max 15 MB)")
    elif sample:
        p = (STATIC / "samples" / Path(sample).name)
        if not p.exists():
            raise HTTPException(404, "sample not found")
        data = p.read_bytes()
    if not text.strip() and data is None:
        raise HTTPException(400, "Send some text or a photo")
    try:
        res = agent.handle_message(user_id, text, data, channel="web")
    except ValueError as err:
        raise HTTPException(404, str(err)) from err
    events.publish("message", user_id=user_id)
    return res


@app.get("/api/history")
def api_history(user_id: str, since_id: int = 0):
    return agent.history(user_id, since_id)


# ---------------------------------------------------------------- dashboard API
def _is_today(ts) -> bool:
    dt = parse_ts(ts)
    return bool(dt) and to_ist(dt).date() == to_ist(utcnow()).date()


@app.get("/api/dashboard")
def api_dashboard():
    db = get_db()
    users = {u["id"]: u for u in db.select("users")}
    all_c = sorted(db.select("complaints"), key=lambda c: c["created_at"] or "", reverse=True)
    today = [c for c in all_c if _is_today(c["created_at"])]
    auto = [c for c in today if c["decision"] in ("auto_refund", "auto_replace", "no_issue_found")]
    timed = [c["resolution_seconds"] for c in auto if c.get("resolution_seconds") is not None]
    lat = [c["vision_latency_ms"] for c in today if c.get("vision_latency_ms")]
    tiles = {
        "complaints_today": len(today),
        "auto_resolved_pct": round(100 * len(auto) / len(today)) if today else 0,
        "avg_resolution_seconds": round(sum(timed) / len(timed), 1) if timed else None,
        "manual_resolution_seconds": MANUAL_RESOLUTION_SECONDS,
        "refund_auto_approved_inr": sum(c.get("refund_amount_inr") or 0 for c in today if c["decision"] in ("auto_refund", "auto_replace")),
        "fraud_flagged": sum(1 for c in today if c.get("fraud_flags")),
        "avg_vision_latency_ms": round(sum(lat) / len(lat)) if lat else None,
        "vision_calls_saved": sum(1 for c in today if (c.get("vision_result") or {}).get("skipped")),
    }
    orders = {o["id"]: o for o in db.select("orders", {"id": list({c["order_id"] for c in all_c[:30]})})} if all_c else {}

    def card(c: dict) -> dict:
        o = orders.get(c["order_id"]) or {}
        vr = c.get("vision_result") or {}
        return {
            "id": c["id"], "user": (users.get(c["user_id"]) or {}).get("name", c["user_id"]), "user_id": c["user_id"],
            "order_id": c["order_id"], "order_items": items_text(o["items"]) if o else "", "order_total": o.get("total"),
            "image_url": c.get("image_url"), "type": c["type"], "user_message": c.get("user_message"),
            "missing": [{"name": m.get("name"), "confidence": m.get("confidence")} for m in vr.get("items_missing", [])]
                       + [{"name": f"{w.get('expected')} (got {w.get('seen')})", "confidence": w.get("confidence")} for w in vr.get("wrong_items", [])],
            "flags": c.get("fraud_flags") or [], "decision": c["decision"], "human_decision": c.get("human_decision"),
            "reason": c.get("decision_reason"), "refund": c.get("refund_amount_inr") or 0, "model": c.get("vision_model"),
            "latency_ms": c.get("vision_latency_ms"), "seconds": c.get("resolution_seconds"), "created_at": c["created_at"],
            "short_reason": vr.get("short_reason"), "confidence": vr.get("overall_confidence"),
            "vision_skipped": bool(vr.get("skipped")),
        }

    feed = [card(c) for c in all_c[:30]]
    queue = [card(c) for c in all_c if c["decision"] == "escalated" and not c.get("human_decision")]
    now = utcnow()
    live = []
    for o in db.select("orders", {"status": LIVE_STATUSES + ["delivered"]}):
        if o["status"] == "delivered" and (not o.get("delivered_at") or now - parse_ts(o["delivered_at"]) > timedelta(minutes=10)):
            continue
        s = status_payload(o, now)
        live.append({"order_id": o["id"], "user": (users.get(o["user_id"]) or {}).get("name", o["user_id"]), "items": s["items"],
                     "stage": o["status"], "stage_label": s["stage_label"], "eta": s["eta_minutes_remaining"],
                     "delayed": s["is_delayed"], "delay_reason": s["delay_reason"], "placed_at": o["placed_at"]})
    live.sort(key=lambda x: x["placed_at"], reverse=True)
    return {"tiles": tiles, "feed": feed, "escalations": queue, "live_orders": live,
            "vision_model": settings.GROQ_VISION_MODEL, "llm_enabled": settings.llm_enabled}


class DecisionIn(BaseModel):
    approve: bool


@app.post("/api/complaints/{complaint_id}/decision")
def api_decide(complaint_id: str, body: DecisionIn):
    res = complaints.human_decide(complaint_id, body.approve)
    if not res:
        raise HTTPException(404, "complaint not found")
    c = res["complaint"]
    user = get_db().get("users", id=c["user_id"]) or {}
    channel = "whatsapp" if str(c["user_id"]).startswith("wa_") else "web"
    agent.post_message(c["user_id"], res["message"], channel=channel)
    sent = whatsapp.send_whatsapp(user.get("phone", ""), res["message"]) if channel == "whatsapp" else False
    events.publish("message", user_id=c["user_id"])
    return {"ok": True, "human_decision": c["human_decision"], "message": res["message"], "whatsapp_sent": sent}


@app.get("/api/events")
async def api_events(request: Request):
    q = events.subscribe()

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- demo controls
@app.post("/demo/advance/{order_id}")
def demo_advance(order_id: str):
    o = simulator.advance(order_id)
    if not o:
        raise HTTPException(404, "order not found")
    return {"order_id": order_id, "status": o["status"]}


@app.post("/demo/reset")
def demo_reset():
    from scripts.seed import seed

    totals = seed(get_db(), do_reset=True, quiet=True)
    agent.reset_sessions()
    events.publish("reset")
    return {"ok": True, "totals": totals}


@app.get("/api/eval")
def api_eval():
    p = ROOT / "eval_results.json"
    if not p.exists():
        return JSONResponse({"available": False})
    data = json.loads(p.read_text(encoding="utf-8"))
    return {"available": data.get("mode") != "offline-mock", **data.get("summary", {}), "mode": data.get("mode")}


# ---------------------------------------------------------------- WhatsApp
@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    return await whatsapp.webhook(request)
