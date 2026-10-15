"""Meta webhook endpoints: GET for verification, POST for events."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..bot.engine import Bot
from ..config import Settings, get_settings
from ..db import get_db
from ..models import OutboundMessage
from ..whatsapp.webhook import parse_webhook, verify_signature
from .deps import get_bot

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


@router.get("/webhook")
def verify(hub_mode: str = Query(alias="hub.mode", default=""),
           hub_verify_token: str = Query(alias="hub.verify_token", default=""),
           hub_challenge: str = Query(alias="hub.challenge", default=""),
           settings: Settings = Depends(get_settings)):
    if hub_mode == "subscribe" and hub_verify_token == settings.wa_verify_token:
        return Response(content=hub_challenge, media_type="text/plain")
    raise HTTPException(403, "verification failed")


@router.post("/webhook")
async def receive(request: Request, db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings), bot: Bot = Depends(get_bot)):
    raw = await request.body()
    if not verify_signature(settings.wa_app_secret, raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(403, "bad signature")
    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(400, "invalid json")

    inbound, statuses = parse_webhook(payload)
    handled = 0
    for msg in inbound:
        try:
            if bot.handle(db, msg):
                handled += 1
        except Exception:  # never let one bad message fail the whole batch
            log.exception("failed handling message %s from %s", msg.wa_message_id, msg.phone)
            db.rollback()
    for st in statuses:
        row = db.scalar(select(OutboundMessage).where(OutboundMessage.wa_message_id == st.wa_message_id))
        if row is not None:
            row.delivery_status = st.status
            if st.error:
                row.error = st.error
    # Meta requires a fast 200; anything else triggers retries.
    return {"handled": handled, "statuses": len(statuses)}
