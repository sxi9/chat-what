"""Builders for WhatsApp Cloud API message payloads.

Meta enforces hard limits on interactive messages. The builders truncate
rather than fail so a long job title never breaks the conversation.
"""
from __future__ import annotations

from dataclasses import dataclass

BUTTON_TITLE_MAX = 20
BUTTONS_MAX = 3
LIST_ROW_TITLE_MAX = 24
LIST_ROW_DESC_MAX = 72
LIST_ROWS_MAX = 10
LIST_BUTTON_MAX = 20
SECTION_TITLE_MAX = 24
BODY_MAX = 1024
HEADER_MAX = 60
FOOTER_MAX = 60
TEXT_MAX = 4096
ID_MAX = 256


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class Button:
    id: str
    title: str


@dataclass(frozen=True)
class Row:
    id: str
    title: str
    description: str = ""


def text_message(to: str, body: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": clip(body, TEXT_MAX)},
    }


def button_message(to: str, body: str, buttons: list[Button], header: str | None = None,
                   footer: str | None = None) -> dict:
    if not 1 <= len(buttons) <= BUTTONS_MAX:
        raise ValueError(f"WhatsApp allows 1-{BUTTONS_MAX} reply buttons, got {len(buttons)}")
    interactive: dict = {
        "type": "button",
        "body": {"text": clip(body, BODY_MAX)},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": clip(b.id, ID_MAX), "title": clip(b.title, BUTTON_TITLE_MAX)}}
                for b in buttons
            ]
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": clip(header, HEADER_MAX)}
    if footer:
        interactive["footer"] = {"text": clip(footer, FOOTER_MAX)}
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }


def list_message(to: str, body: str, button_label: str, rows: list[Row],
                 section_title: str = "Options", header: str | None = None,
                 footer: str | None = None) -> dict:
    if not 1 <= len(rows) <= LIST_ROWS_MAX:
        raise ValueError(f"WhatsApp allows 1-{LIST_ROWS_MAX} list rows, got {len(rows)}")
    interactive: dict = {
        "type": "list",
        "body": {"text": clip(body, BODY_MAX)},
        "action": {
            "button": clip(button_label, LIST_BUTTON_MAX),
            "sections": [
                {
                    "title": clip(section_title, SECTION_TITLE_MAX),
                    "rows": [
                        {
                            "id": clip(r.id, ID_MAX),
                            "title": clip(r.title, LIST_ROW_TITLE_MAX),
                            **({"description": clip(r.description, LIST_ROW_DESC_MAX)} if r.description else {}),
                        }
                        for r in rows
                    ],
                }
            ],
        },
    }
    if header:
        interactive["header"] = {"type": "text", "text": clip(header, HEADER_MAX)}
    if footer:
        interactive["footer"] = {"text": clip(footer, FOOTER_MAX)}
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }


def template_message(to: str, name: str, language: str, body_params: list[str]) -> dict:
    """A pre-approved template. Required outside the 24-hour service window."""
    components = []
    if body_params:
        components.append(
            {
                "type": "body",
                "parameters": [{"type": "text", "text": clip(p, 1024)} for p in body_params],
            }
        )
    return {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": name, "language": {"code": language}, "components": components},
    }
