"""Sending side. `Sender` is the only thing the bot depends on, so the Meta
client can be replaced by Twilio or a test double without touching the bot."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

log = logging.getLogger(__name__)


@dataclass
class SendResult:
    ok: bool
    wa_message_id: str | None = None
    error: str = ""


class Sender(Protocol):
    def send(self, payload: dict) -> SendResult: ...


class MetaCloudClient:
    """WhatsApp Cloud API: POST /{version}/{phone_number_id}/messages."""

    def __init__(self, access_token: str, phone_number_id: str, api_version: str = "v21.0",
                 timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self._url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
        self._headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def send(self, payload: dict) -> SendResult:
        try:
            resp = self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError as exc:
            log.warning("WhatsApp send failed: %s", exc)
            return SendResult(ok=False, error=str(exc))
        if resp.status_code >= 400:
            log.warning("WhatsApp send rejected %s: %s", resp.status_code, resp.text[:500])
            return SendResult(ok=False, error=f"{resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        msg_id = (data.get("messages") or [{}])[0].get("id")
        return SendResult(ok=True, wa_message_id=msg_id)

    def mark_read(self, wa_message_id: str) -> None:
        payload = {"messaging_product": "whatsapp", "status": "read", "message_id": wa_message_id}
        try:
            self._client.post(self._url, headers=self._headers, json=payload)
        except httpx.HTTPError:
            pass


@dataclass
class RecordingSender:
    """Test double and local simulator: stores payloads instead of sending them."""

    sent: list[dict] = field(default_factory=list)
    fail: bool = False

    def send(self, payload: dict) -> SendResult:
        self.sent.append(payload)
        if self.fail:
            return SendResult(ok=False, error="simulated failure")
        return SendResult(ok=True, wa_message_id=f"wamid.test.{len(self.sent)}")

    def last(self) -> dict:
        return self.sent[-1]

    def clear(self) -> None:
        self.sent.clear()
