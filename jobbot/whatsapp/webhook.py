"""Inbound side: signature verification and normalisation of webhook payloads."""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass


@dataclass(frozen=True)
class Inbound:
    """One user message, normalised so the bot never sees raw WhatsApp JSON.

    `text` holds the typed text for text messages, or the button/row *id* for
    interactive replies. `label` holds the human title of the tapped item.
    """

    wa_message_id: str
    phone: str
    kind: str  # "text" | "button" | "list" | "other"
    text: str
    label: str = ""
    profile_name: str = ""
    timestamp: int = 0


@dataclass(frozen=True)
class StatusUpdate:
    wa_message_id: str
    status: str  # sent | delivered | read | failed
    error: str = ""


def verify_signature(app_secret: str, raw_body: bytes, header: str | None) -> bool:
    """X-Hub-Signature-256 is 'sha256=<hex hmac of body with the app secret>'."""
    if not app_secret:
        return True  # signature checking disabled (local development)
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len("sha256="):])


def parse_webhook(payload: dict) -> tuple[list[Inbound], list[StatusUpdate]]:
    inbound: list[Inbound] = []
    statuses: list[StatusUpdate] = []
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value") or {}
            names = {c.get("wa_id"): (c.get("profile") or {}).get("name", "")
                     for c in value.get("contacts", []) or []}
            for msg in value.get("messages", []) or []:
                inbound.append(_parse_message(msg, names))
            for st in value.get("statuses", []) or []:
                errors = st.get("errors") or []
                err = ""
                if errors:
                    e = errors[0]
                    err = f"{e.get('code', '')} {e.get('title', '')}".strip()
                statuses.append(StatusUpdate(st.get("id", ""), st.get("status", ""), err))
    return inbound, statuses


def _parse_message(msg: dict, names: dict[str, str]) -> Inbound:
    phone = msg.get("from", "")
    base = dict(
        wa_message_id=msg.get("id", ""),
        phone=phone,
        profile_name=names.get(phone, ""),
        timestamp=int(msg.get("timestamp") or 0),
    )
    mtype = msg.get("type")
    if mtype == "text":
        return Inbound(kind="text", text=(msg.get("text") or {}).get("body", "").strip(), **base)
    if mtype == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "button_reply":
            r = inter["button_reply"]
            return Inbound(kind="button", text=r.get("id", ""), label=r.get("title", ""), **base)
        if inter.get("type") == "list_reply":
            r = inter["list_reply"]
            return Inbound(kind="list", text=r.get("id", ""), label=r.get("title", ""), **base)
    if mtype == "button":  # reply to a template quick-reply button
        b = msg.get("button") or {}
        return Inbound(kind="button", text=b.get("payload", ""), label=b.get("text", ""), **base)
    return Inbound(kind="other", text="", **base)
