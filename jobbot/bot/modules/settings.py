"""Settings module: notifications toggle, job preferences, name."""
from __future__ import annotations

from ...whatsapp import messages as m
from ..context import Ctx
from . import MenuEntry, Module

ASK_NAME = "SETTINGS_ASK_NAME"

BODY = "Notifications: *{notify}*\nName: {name}\nJob keywords: {keywords}\nLocation: {location}"
NOTIFY_ON = "Notifications are on. Reply STOP at any time to pause them."
NOTIFY_OFF = "Notifications are off."
ASK_NAME_TXT = "What should I call you?"
NAME_SAVED = "Name updated."


class SettingsModule(Module):
    key = "settings"
    entries = (MenuEntry("settings", "Settings", "Notifications, name and job preferences"),)
    commands = ("settings",)
    keywords = {"settings": "settings", "preferences": "settings", "notifications": "settings"}
    states = (ASK_NAME,)

    def open(self, ctx: Ctx, entry: str) -> None:
        self.show(ctx)

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        if arg == "notify_on":
            ctx.bot.set_notifications(ctx, True)
            self.show(ctx, NOTIFY_ON)
        elif arg == "notify_off":
            ctx.bot.set_notifications(ctx, False)
            self.show(ctx, NOTIFY_OFF)
        elif arg == "name":
            ctx.set_state(ASK_NAME)
            ctx.text(ASK_NAME_TXT, kind="ask_name")
        else:
            self.show(ctx)

    def text(self, ctx: Ctx, text: str) -> None:
        if ctx.state == ASK_NAME and text.strip():
            ctx.user.name = text.strip()[:120]
            self.show(ctx, NAME_SAVED)
        else:
            ctx.text(ASK_NAME_TXT, kind="ask_name")

    def show(self, ctx: Ctx, preface: str | None = None) -> None:
        ctx.set_state("MENU")
        u = ctx.user
        body = BODY.format(notify="On" if u.notifications_opt_in else "Off", name=u.name or "not set",
                           keywords=u.keywords or "not set", location=u.location or "any")
        if preface:
            body = f"{preface}\n\n{body}"
        toggle = (m.Button("settings:notify_off", "Notifications off") if u.notifications_opt_in
                  else m.Button("settings:notify_on", "Notifications on"))
        ctx.buttons(body, [toggle, m.Button("prefs:edit", "Job preferences"), m.Button("settings:name", "Change name")],
                    header="Settings", footer="Send MENU to go back", kind="settings")
