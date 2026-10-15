"""Contact-us module: the user writes a message, it becomes a ticket the team answers
from the admin API, and the answer is delivered back on WhatsApp."""
from __future__ import annotations

from ...models import SupportTicket, TicketStatus
from ...whatsapp import messages as m
from ..context import Ctx
from . import MenuEntry, Module

ASK_MESSAGE = "CONTACT_ASK_MESSAGE"

ASK = "Type your message for our team and send it. One message is enough; we'll reply here."
RECEIVED = "Thanks, I've passed that on. Ticket T{ref}. Someone from the team will reply here."
TOO_SHORT = "Please send a bit more detail so the team can help."


class ContactModule(Module):
    key = "contact"
    entries = (MenuEntry("contact", "Contact us", "Message our team, we reply here"),)
    commands = ("contact",)
    keywords = {"contact": "contact", "contact us": "contact", "support": "contact", "agent": "contact",
                "human": "contact", "talk to someone": "contact"}
    states = (ASK_MESSAGE,)

    def open(self, ctx: Ctx, entry: str) -> None:
        ctx.set_state(ASK_MESSAGE)
        ctx.buttons(ASK, [m.Button("menu:main", "Cancel")], header="Contact us", kind="contact_ask")

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        self.open(ctx, "contact")

    def text(self, ctx: Ctx, text: str) -> None:
        body = text.strip()
        if len(body) < 3:
            ctx.text(TOO_SHORT, kind="note")
            return
        ticket = SupportTicket(user_id=ctx.user.id, message=body[:4000], status=TicketStatus.open)
        ctx.db.add(ticket)
        ctx.db.flush()
        ctx.set_state("MENU")
        ctx.buttons(RECEIVED.format(ref=ticket.id), [m.Button("menu:main", "Main menu")], kind="contact_received")
