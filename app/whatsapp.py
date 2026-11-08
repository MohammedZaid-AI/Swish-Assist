"""Twilio WhatsApp webhook: POST /whatsapp.

Reads Body, From, NumMedia, MediaUrl0; downloads the image with Twilio basic auth; runs the agent;
replies with TwiML. Validates X-Twilio-Signature when TWILIO_AUTH_TOKEN is set.
Unknown numbers become new users with a copy of Sneha's just-delivered order so the photo flow works instantly.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from xml.sax.saxutils import escape

from fastapi import Request
from fastapi.responses import Response

from . import agent, llm
from .config import settings
from .db import get_db
from .models import iso, utcnow

log = logging.getLogger("swish.whatsapp")

SYNC_BUDGET_SECONDS = 12  # Twilio waits 15 s for the webhook; answer before that
DEMO_TEMPLATE_ORDER = "SW5005"


def normalize_phone(raw: str) -> str:
    return (raw or "").replace("whatsapp:", "").strip()


def user_for_phone(phone: str, profile_name: str | None = None) -> dict:
    db = get_db()
    user = db.get("users", phone=phone)
    if user:
        return user
    uid = "wa_" + re.sub(r"\D", "", phone)[-12:]
    user = {"id": uid, "phone": phone, "name": (profile_name or "Guest").strip()[:40] or "Guest",
            "language_pref": "en", "created_at": iso(utcnow())}
    db.insert("users", user)
    give_demo_order(uid)
    log.info("new WhatsApp user %s", uid)
    return user


def give_demo_order(user_id: str) -> dict | None:
    """Copy Sneha's recent delivered order (fries, burger, cold coffee) so a new tester can try the photo flow."""
    db = get_db()
    tpl = db.get("orders", id=DEMO_TEMPLATE_ORDER)
    now = utcnow()
    items = tpl["items"] if tpl else [
        {"menu_item_id": "fries", "name": "Classic Fries", "qty": 1, "price": 99},
        {"menu_item_id": "chicken_burger", "name": "Grilled Chicken Cheese Burger", "qty": 1, "price": 179},
        {"menu_item_id": "cold_coffee", "name": "Cold Coffee", "qty": 1, "price": 119}]
    subtotal = sum(i["price"] * i["qty"] for i in items)
    order = {"id": f"SW{user_id[-6:].upper()}", "user_id": user_id, "items": items, "subtotal": subtotal, "discount": 0,
             "total": subtotal, "coupon_code": None, "status": "delivered", "placed_at": iso(now - timedelta(minutes=16)),
             "preparing_at": iso(now - timedelta(minutes=15)), "packed_at": iso(now - timedelta(minutes=11)),
             "out_for_delivery_at": iso(now - timedelta(minutes=10)), "delivered_at": iso(now - timedelta(minutes=6)),
             "eta_minutes": 10, "delay_reason": None, "kitchen_name": "Swish HSR Sector 2", "kitchen_distance_km": 0.7,
             "rider_name": "Ravi"}
    if not db.get("orders", id=order["id"]):
        db.insert("orders", order)
    return order


def download_media(url: str) -> bytes:
    auth = (settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN) if settings.TWILIO_ACCOUNT_SID else None
    with llm.http_client(timeout=20) as c:
        r = c.get(url, auth=auth)
        r.raise_for_status()
        return r.content


def validate_signature(url: str, params: dict, signature: str) -> bool:
    if not settings.TWILIO_AUTH_TOKEN:
        return True
    from twilio.request_validator import RequestValidator

    return RequestValidator(settings.TWILIO_AUTH_TOKEN).validate(url, params, signature or "")


def twiml(text: str) -> str:
    body = f"<Message>{escape(text)}</Message>" if text else ""
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'


def can_send() -> bool:
    return bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_WHATSAPP_NUMBER)


def send_whatsapp(phone: str, text: str) -> bool:
    """Push a message outside the webhook reply (human decisions, slow replies)."""
    if not can_send() or not phone:
        return False
    try:
        from twilio.http.http_client import TwilioHttpClient
        from twilio.rest import Client

        frm = settings.TWILIO_WHATSAPP_NUMBER
        frm = frm if frm.startswith("whatsapp:") else f"whatsapp:{frm}"
        Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN, http_client=TwilioHttpClient()).messages.create(
            from_=frm, to=f"whatsapp:{phone}", body=text[:1500])
        return True
    except Exception:  # noqa: BLE001
        log.exception("WhatsApp send failed")
        return False


async def webhook(request: Request) -> Response:
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = (settings.PUBLIC_BASE_URL + "/whatsapp") if settings.PUBLIC_BASE_URL else str(request.url)
    if not validate_signature(url, params, request.headers.get("X-Twilio-Signature", "")):
        log.warning("bad Twilio signature")
        return Response("invalid signature", status_code=403)

    phone = normalize_phone(params.get("From", ""))
    if not phone:
        return Response(twiml(""), media_type="application/xml")
    user = await asyncio.to_thread(user_for_phone, phone, params.get("ProfileName"))
    text = params.get("Body", "")
    image = None
    try:
        n_media = int(params.get("NumMedia") or 0)
    except ValueError:
        n_media = 0
    if n_media > 0 and params.get("MediaUrl0") and (params.get("MediaContentType0") or "image/").startswith("image/"):
        try:
            image = await asyncio.to_thread(download_media, params["MediaUrl0"])
        except Exception:  # noqa: BLE001
            log.exception("media download failed")
            return Response(twiml("I couldn't open that photo. Could you send it again?"), media_type="application/xml")

    def notice(msg: str) -> None:
        send_whatsapp(phone, msg)

    task = asyncio.ensure_future(asyncio.to_thread(agent.handle_message, user["id"], text, image, "whatsapp", notice))
    try:
        res = await asyncio.wait_for(asyncio.shield(task), timeout=SYNC_BUDGET_SECONDS)
        return Response(twiml(res["reply"]), media_type="application/xml")
    except asyncio.TimeoutError:
        # Too slow for Twilio's 15 s window: acknowledge now, push the answer when it is ready.
        def deliver(t: asyncio.Future) -> None:
            if not t.exception():
                send_whatsapp(phone, t.result()["reply"])
        task.add_done_callback(deliver)
        return Response(twiml("One sec, checking…"), media_type="application/xml")
