"""Module contract. A module is a self-contained feature the main menu links to.

To add a feature: subclass `Module`, declare the menu entries, the button-id
prefixes and the conversation states it owns, and register it in `engine.build_bot`.
The core handles onboarding, the menu, STOP, idempotency and anything unrecognised.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..context import Ctx


@dataclass(frozen=True)
class MenuEntry:
    id: str            # becomes the command "menu:<id>"
    title: str         # button / row title (<= 20 chars for buttons, 24 for rows)
    description: str = ""  # row description when the menu renders as a list


class Module:
    key: str = ""
    entries: tuple[MenuEntry, ...] = ()
    commands: tuple[str, ...] = ()   # button/list id prefixes this module handles, e.g. "job"
    keywords: dict[str, str] = {}    # lowercase text -> menu entry id, e.g. {"jobs": "jobs"}
    states: tuple[str, ...] = ()     # states in which this module asked a question and expects text

    def open(self, ctx: Ctx, entry: str) -> None:
        """User chose one of this module's menu entries."""
        raise NotImplementedError

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        """User tapped a button/row whose id is `<action>:<arg>` and `action` is in `commands`."""
        raise NotImplementedError

    def text(self, ctx: Ctx, text: str) -> None:
        """User typed free text while in one of this module's states."""
        ctx.menu()
