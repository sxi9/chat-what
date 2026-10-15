"""What a module gets handed for every interaction: the user, the db and reply helpers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import User
from ..services.notify import Notifier
from ..whatsapp import messages as m

if TYPE_CHECKING:
    from .engine import Bot

log = logging.getLogger(__name__)


@dataclass
class Ctx:
    db: Session
    user: User
    bot: "Bot"
    notifier: Notifier
    settings: Settings

    # ----- state -------------------------------------------------------------

    @property
    def state(self) -> str:
        return self.user.conversation.state

    @property
    def data(self) -> dict:
        try:
            return json.loads(self.user.conversation.context or "{}")
        except json.JSONDecodeError:
            return {}

    def set_state(self, state: str, data: dict | None = None) -> None:
        self.user.conversation.state = state
        self.user.conversation.context = json.dumps(data or {})

    # ----- replies -----------------------------------------------------------

    def send(self, kind: str, payload: dict) -> None:
        result = self.notifier.send(self.db, self.user, kind, payload)
        if not result.ok:
            log.warning("send %s to %s failed: %s", kind, self.user.phone, result.error)

    def text(self, body: str, kind: str = "text") -> None:
        self.send(kind, m.text_message(self.user.phone, body))

    def buttons(self, body: str, buttons: list[m.Button], header: str | None = None,
                footer: str | None = None, kind: str = "buttons") -> None:
        self.send(kind, m.button_message(self.user.phone, body, buttons, header=header, footer=footer))

    def list(self, body: str, button_label: str, rows: list[m.Row], section_title: str = "Options",
             header: str | None = None, footer: str | None = None, kind: str = "list") -> None:
        self.send(kind, m.list_message(self.user.phone, body, button_label, rows,
                                       section_title=section_title, header=header, footer=footer))

    def menu(self, preface: str | None = None) -> None:
        self.bot.show_menu(self, preface)
