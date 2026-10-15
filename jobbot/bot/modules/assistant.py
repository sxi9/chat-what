"""Assistant module: free text goes to the model; proposed actions need a tap to execute."""
from __future__ import annotations

import json

from ...assistant.agent import Agent
from ...models import PendingAction, PendingActionStatus
from ...services import applications as apps
from ...services import jobs as jobsvc
from ...whatsapp import messages as m
from ..context import Ctx
from . import MenuEntry, Module

CHAT = "ASSISTANT_CHAT"
INTRO = ("Ask me anything in your own words: what jobs suit you, where an application stands, "
         "or how this works. Send MENU any time to go back to the buttons.")
CONFIRMED_APPLY = "Done. You've applied for *{title}* at {company}. Ref A{ref}. I'll tell you when the status changes."
CONFIRMED_WITHDRAW = "Done. Your application for *{title}* has been withdrawn."
CANCELLED = "Cancelled. Nothing was changed."
EXPIRED = "That request is no longer pending."
MAX_LEN = 3900


class AssistantModule(Module):
    key = "assistant"
    entries = (MenuEntry("assistant", "Ask anything", "Chat in your own words"),)
    commands = ("confirm", "cancel", "assistant")
    keywords = {"ask": "assistant", "chat": "assistant", "assistant": "assistant"}
    states = (CHAT,)

    def __init__(self, agent: Agent):
        self.agent = agent

    def open(self, ctx: Ctx, entry: str) -> None:
        ctx.set_state(CHAT)
        ctx.buttons(INTRO, [m.Button("menu:main", "Main menu")], kind="assistant_intro")

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        if action in ("confirm", "cancel") and arg.isdigit():
            self._resolve(ctx, int(arg), confirmed=(action == "confirm"))
        else:
            self.open(ctx, "assistant")

    def text(self, ctx: Ctx, text: str) -> None:
        """Called for free text in CHAT state and, as the engine's fallback, for any unrecognised text."""
        ctx.set_state(CHAT)
        reply = self.agent.reply(ctx.db, ctx.user, text)
        chunks = _split(reply.text)
        for chunk in chunks[:-1]:
            ctx.text(chunk, kind="assistant")
        if reply.pending is not None:
            ctx.buttons(chunks[-1], [m.Button(f"confirm:{reply.pending.id}", "Confirm"),
                                     m.Button(f"cancel:{reply.pending.id}", "Cancel")], kind="assistant_confirm")
        else:
            ctx.text(chunks[-1], kind="assistant")

    def _resolve(self, ctx: Ctx, pa_id: int, confirmed: bool) -> None:
        pa = ctx.db.get(PendingAction, pa_id)
        if pa is None or pa.user_id != ctx.user.id or pa.status != PendingActionStatus.pending:
            ctx.text(EXPIRED, kind="assistant_note")
            return
        if not confirmed:
            pa.status = PendingActionStatus.cancelled
            ctx.text(CANCELLED, kind="assistant_note")
            return
        payload = json.loads(pa.payload or "{}")
        pa.status = PendingActionStatus.confirmed
        if pa.kind == "apply":
            job = jobsvc.get_job(ctx.db, int(payload["job_id"]))
            if job is None or not job.is_active:
                ctx.text("That job has closed in the meantime, so nothing was done.", kind="assistant_note")
                return
            app, _ = apps.apply(ctx.db, ctx.user, job)
            ctx.buttons(CONFIRMED_APPLY.format(title=job.title, company=job.company, ref=app.id),
                        [m.Button("menu:applications", "My applications"), m.Button("menu:main", "Main menu")],
                        kind="assistant_done")
        elif pa.kind == "withdraw":
            app = apps.get_for_user(ctx.db, ctx.user, int(payload["application_id"]))
            if app is None or not apps.withdraw(ctx.db, app):
                ctx.text("That application can't be withdrawn any more.", kind="assistant_note")
                return
            ctx.text(CONFIRMED_WITHDRAW.format(title=app.job.title), kind="assistant_done")
        else:
            ctx.text(EXPIRED, kind="assistant_note")


def _split(text: str, limit: int = MAX_LEN) -> list[str]:
    text = text.strip() or "…"
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for para in text.split("\n"):
        if len(cur) + len(para) + 1 > limit and cur:
            parts.append(cur.rstrip())
            cur = ""
        while len(para) > limit:
            parts.append(para[:limit])
            para = para[limit:]
        cur += para + "\n"
    if cur.strip():
        parts.append(cur.rstrip())
    return parts
