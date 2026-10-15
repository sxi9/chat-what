"""The natural-language brain. `Agent` is a protocol so tests and the simulator can use a fake."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from ..bot.modules.faq import FaqItem
from ..config import Settings
from ..models import PendingAction, Reminder, User, utcnow
from . import memory
from .tools import Side, build_tools

log = logging.getLogger(__name__)

PERSONA = """You are {name}, a personal assistant that people reach over WhatsApp. You help them find work,
apply, track applications, and sort out anything about this service. You are warm, direct and brief.

Rules
- You are on WhatsApp. Keep replies short: a few sentences, or a short list. Use *single asterisks* for bold.
  No markdown headers, no tables, no links unless the user asks for one.
- Use tools for anything about jobs, applications, preferences or the service. Never invent a job, a status
  or a policy. If a tool returns nothing, say so plainly.
- Actions with consequences (applying, withdrawing) never happen without the user's confirmation. When a
  tool says CONFIRMATION REQUIRED, state in one sentence what will happen if they confirm, then stop.
  Confirm and Cancel buttons appear under your message. Do not call that tool again in the same turn.
- Remember useful facts the user tells you about themselves with the remember tool (skills, licence,
  availability, salary expectations), and use what you already know.
- If you promise to follow up later, schedule it with schedule_reminder so it actually happens.
- If something needs a human (complaints, data deletion, payment, anything you can't do), use contact_team
  and tell the user a person will reply here.
- Never ask for passwords, payment details or ID numbers.
- Reply in the user's language."""


@dataclass
class AgentReply:
    text: str
    pending: PendingAction | None = None
    reminders: list[Reminder] = field(default_factory=list)


class Agent(Protocol):
    def reply(self, db: Session, user: User, text: str) -> AgentReply: ...


def _profile_block(db: Session, user: User) -> str:
    mems = memory.memories(db, user)
    lines = [
        f"User profile: name={user.name or 'unknown'}; job keywords={user.keywords or 'not set'}; "
        f"location={user.location or 'not set'}; notifications={'on' if user.notifications_opt_in else 'off'}; "
        f"applications={len(user.applications)}.",
        "Now (UTC): " + utcnow().strftime("%A %Y-%m-%d %H:%M"),
    ]
    if mems:
        lines.append("Known facts: " + "; ".join(f"{m.key}: {m.value}" for m in mems[:30]))
    return "\n".join(lines)


class ClaudeAgent:
    def __init__(self, settings: Settings, faq_items: list[FaqItem], client=None):
        import anthropic

        self.settings = settings
        self.faq_items = faq_items
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key or None, timeout=60.0)

    def reply(self, db: Session, user: User, text: str) -> AgentReply:
        import anthropic

        side = Side()
        tools: list = build_tools(db, user, side, self.settings, self.faq_items)
        if self.settings.assistant_web_search:
            tools.append({"type": "web_search_20260209", "name": "web_search", "max_uses": 3})
        history = memory.recent_messages(db, user, self.settings.assistant_history_turns)
        messages = history + [{"role": "user", "content": text}]
        system = [
            {"type": "text", "text": PERSONA.format(name=self.settings.assistant_name),
             "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": _profile_block(db, user)},
        ]
        try:
            runner = self.client.beta.messages.tool_runner(
                model=self.settings.assistant_model,
                max_tokens=self.settings.assistant_max_tokens,  # WhatsApp messages are short
                system=system,
                tools=tools,
                messages=messages,
                max_iterations=8,
                output_config={"effort": self.settings.assistant_effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            final = runner.until_done()
        except anthropic.RateLimitError:
            log.warning("assistant rate limited")
            return AgentReply("I'm a little busy right now. Give me a minute and try again, or use the menu.")
        except anthropic.APIStatusError as exc:
            log.error("assistant API error %s: %s", exc.status_code, exc.message)
            return AgentReply("I couldn't think that one through just now. The menu still works. Send MENU.")
        except anthropic.APIConnectionError as exc:
            log.error("assistant connection error: %s", exc)
            return AgentReply("I can't reach my brain right now. Please try again shortly, or send MENU.")

        if final.stop_reason == "refusal":
            return AgentReply("I can't help with that one. Is there something about work or your applications I can do?")
        reply_text = "".join(b.text for b in final.content if b.type == "text").strip()
        if not reply_text:
            reply_text = "Done." if side.pending is None else "Please confirm below."
        memory.add_message(db, user, "user", text)
        memory.add_message(db, user, "assistant", reply_text)
        return AgentReply(reply_text, pending=side.pending, reminders=side.reminders)


class FakeAgent:
    """Deterministic stand-in for tests and the offline simulator. Understands a few phrases."""

    def __init__(self, settings: Settings, faq_items: list[FaqItem]):
        self.settings, self.faq_items = settings, faq_items
        self.calls: list[str] = []

    def reply(self, db: Session, user: User, text: str) -> AgentReply:
        self.calls.append(text)
        side = Side()
        tools = {t.name: t for t in build_tools(db, user, side, self.settings, self.faq_items)}
        lowered = text.lower()
        memory.add_message(db, user, "user", text)
        if lowered.startswith("apply ") and lowered.split()[-1].isdigit():
            tools["apply_to_job"].call({"job_id": int(lowered.split()[-1])})
            out = f"I'll apply for job {lowered.split()[-1]} once you confirm."
        elif lowered.startswith("withdraw ") and lowered.split()[-1].isdigit():
            tools["withdraw_application"].call({"application_id": int(lowered.split()[-1])})
            out = "I'll withdraw it once you confirm."
        elif lowered.startswith("remember "):
            tools["remember"].call({"key": "note", "value": text[9:]})
            out = "Got it, I'll remember that."
        elif lowered.startswith("remind me"):
            tools["schedule_reminder"].call({"hours_from_now": 24, "message": "You asked me to check in. How is the search going?"})
            out = "I'll check in with you tomorrow."
        elif "jobs" in lowered:
            import json
            n = len(json.loads(tools["search_jobs"].call({}))["results"])
            out = f"I found {n} jobs for you."
        else:
            out = f"[fake assistant] You said: {text}"
        memory.add_message(db, user, "assistant", out)
        return AgentReply(out, pending=side.pending, reminders=side.reminders)
