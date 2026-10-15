"""Shared FastAPI dependencies: settings, sender, notifier, bot, admin auth."""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from ..bot.engine import Bot, build_bot
from ..config import Settings, get_settings
from ..services.notify import Notifier
from ..whatsapp.client import MetaCloudClient, RecordingSender, Sender

_sender_override: Sender | None = None
_agent_override = None


def set_sender(sender: Sender | None) -> None:
    """Tests and the local simulator inject a sender here."""
    global _sender_override
    _sender_override = sender
    get_notifier.cache_clear()
    get_bot.cache_clear()


def set_agent(agent) -> None:
    """Tests inject a fake assistant brain here."""
    global _agent_override
    _agent_override = agent
    get_bot.cache_clear()


def get_sender(settings: Settings | None = None) -> Sender:
    if _sender_override is not None:
        return _sender_override
    settings = settings or get_settings()
    if not settings.wa_access_token or not settings.wa_phone_number_id:
        # No credentials: record instead of crashing, so the API still runs locally.
        return RecordingSender()
    return MetaCloudClient(settings.wa_access_token, settings.wa_phone_number_id, settings.wa_api_version)


@lru_cache
def get_notifier() -> Notifier:
    settings = get_settings()
    return Notifier(sender=get_sender(settings), settings=settings)


@lru_cache
def get_bot() -> Bot:
    return build_bot(get_notifier(), get_settings(), agent=_agent_override)


def require_admin(x_admin_key: str | None = Header(default=None),
                  settings: Settings = Depends(get_settings)) -> None:
    if not x_admin_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing X-Admin-Key")
