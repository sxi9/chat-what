"""Core of the deterministic bot: onboarding, main menu, global commands, routing.

Rules of the machine:
  * Button and list ids are commands and work from any state, so a user can tap an
    old message and still get a sensible answer. The prefix before ':' picks the module.
  * STOP and a few menu words are global text shortcuts.
  * Free text is interpreted only by the module that owns the current state, and only
    when that state asked a question.
  * Anything else gets the main menu. The bot never guesses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import ConversationState, ProcessedMessage, User, UserStatus, utcnow
from ..services.notify import Notifier
from ..whatsapp import messages as m
from ..whatsapp.webhook import Inbound
from . import copy
from .context import Ctx
from .modules import Module

log = logging.getLogger(__name__)

# Core states
ASK_NAME = "ASK_NAME"
ASK_NOTIFY = "ASK_NOTIFY"
MENU = "MENU"
ONBOARDING_STATES = {ASK_NAME, ASK_NOTIFY}

STOP_WORDS = {"stop", "unsubscribe", "pause", "stop alerts", "stop notifications"}
MENU_WORDS = {"menu", "hi", "hello", "hey", "start", "help", "home", "main menu", "back"}
YES_WORDS = {"yes", "y", "ok", "okay", "sure", "yes please"}
NO_WORDS = {"no", "n", "not now", "later", "nope"}


@dataclass
class Bot:
    notifier: Notifier
    settings: Settings
    modules: list[Module] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_command: dict[str, Module] = {}
        self._by_entry: dict[str, Module] = {}
        self._by_state: dict[str, Module] = {}
        self._by_keyword: dict[str, tuple[Module, str]] = {}
        for mod in self.modules:
            self.register(mod)

    def register(self, mod: Module) -> None:
        if mod not in self.modules:
            self.modules.append(mod)
        for c in mod.commands:
            self._by_command[c] = mod
        for e in mod.entries:
            self._by_entry[e.id] = mod
        for s in mod.states:
            self._by_state[s] = mod
        for word, entry in mod.keywords.items():
            self._by_keyword[word.lower()] = (mod, entry)

    # ------------------------------------------------------------------ entry

    def handle(self, db: Session, msg: Inbound) -> bool:
        """Process one inbound message. Returns False if it was a duplicate."""
        if msg.wa_message_id:
            if db.get(ProcessedMessage, msg.wa_message_id):
                return False
            db.add(ProcessedMessage(wa_message_id=msg.wa_message_id))
            db.flush()

        user = db.query(User).filter_by(phone=msg.phone).one_or_none()
        now = utcnow()
        if user is None:
            user = User(phone=msg.phone, name=None, last_inbound_at=now)
            user.conversation = ConversationState(state=ASK_NAME)
            db.add(user)
            db.flush()
            Ctx(db, user, self, self.notifier, self.settings).text(copy.WELCOME, kind="welcome")
            return True

        user.last_inbound_at = now
        if user.status == UserStatus.blocked:
            return True
        if user.conversation is None:
            user.conversation = ConversationState(state=MENU)
            db.flush()

        ctx = Ctx(db, user, self, self.notifier, self.settings)
        if msg.kind in ("button", "list"):
            self._command(ctx, msg.text)
        elif msg.kind == "text":
            self._text(ctx, msg.text)
        else:
            self.show_menu(ctx, copy.MENU_UNSUPPORTED)
        return True

    # --------------------------------------------------------------- routing

    def _text(self, ctx: Ctx, text: str) -> None:
        lowered = " ".join(text.lower().split())
        if lowered in STOP_WORDS:
            self._opt_out(ctx)
            return
        if ctx.state in ONBOARDING_STATES:
            self._onboarding_answer(ctx, text)
            return
        owner = self._by_state.get(ctx.state)
        if owner is not None:
            if lowered in MENU_WORDS:
                self.show_menu(ctx)
            else:
                owner.text(ctx, text)
            return
        if lowered in MENU_WORDS:
            self.show_menu(ctx)
            return
        hit = self._by_keyword.get(lowered)
        if hit is not None:
            mod, entry = hit
            mod.open(ctx, entry)
            return
        self.show_menu(ctx, copy.MENU_UNKNOWN)

    def _command(self, ctx: Ctx, cmd: str) -> None:
        action, _, arg = cmd.partition(":")
        if ctx.state == ASK_NOTIFY and action == "notify":
            self._finish_onboarding(ctx, opt_in=(arg == "on"))
            return
        if ctx.user.status != UserStatus.active:
            self._ask_current_question(ctx)  # tapped a stale button mid-onboarding
            return
        if action == "menu":
            if arg in ("", "main"):
                self.show_menu(ctx)
                return
            mod = self._by_entry.get(arg)
            if mod is None:
                self.show_menu(ctx, copy.MENU_UNKNOWN)
            else:
                mod.open(ctx, arg)
            return
        if action == "notify" and arg in ("on", "off"):
            self.set_notifications(ctx, arg == "on")
            ctx.menu(copy.NOTIFY_ON if arg == "on" else copy.NOTIFY_OFF)
            return
        mod = self._by_command.get(action)
        if mod is None:
            self.show_menu(ctx, copy.MENU_UNKNOWN)
        else:
            mod.command(ctx, action, arg)

    # ------------------------------------------------------------ onboarding

    def _onboarding_answer(self, ctx: Ctx, text: str) -> None:
        answer = text.strip()
        if not answer:
            self._ask_current_question(ctx)
            return
        if ctx.state == ASK_NAME:
            ctx.user.name = answer[:120]
            ctx.set_state(ASK_NOTIFY)
            self._ask_notify(ctx)
        elif ctx.state == ASK_NOTIFY:
            lowered = answer.lower()
            if lowered in YES_WORDS:
                self._finish_onboarding(ctx, opt_in=True)
            elif lowered in NO_WORDS:
                self._finish_onboarding(ctx, opt_in=False)
            else:
                self._ask_notify(ctx)

    def _ask_current_question(self, ctx: Ctx) -> None:
        if ctx.state == ASK_NAME:
            ctx.text(copy.WELCOME, kind="welcome")
        elif ctx.state == ASK_NOTIFY:
            self._ask_notify(ctx)
        else:
            self.show_menu(ctx)

    def _ask_notify(self, ctx: Ctx) -> None:
        ctx.buttons(copy.ASK_NOTIFY.format(name=ctx.user.name or ""),
                    [m.Button("notify:on", copy.BTN_YES), m.Button("notify:off", copy.BTN_NO)],
                    kind="ask_notify")

    def _finish_onboarding(self, ctx: Ctx, opt_in: bool) -> None:
        ctx.user.status = UserStatus.active
        self.set_notifications(ctx, opt_in)
        intro = copy.ONBOARDING_DONE.format(name=ctx.user.name or "") + " " + (
            copy.NOTIFY_ON if opt_in else copy.NOTIFY_OFF)
        self.show_menu(ctx, intro)

    # ------------------------------------------------------------------ menu

    def show_menu(self, ctx: Ctx, preface: str | None = None) -> None:
        ctx.set_state(MENU)
        body = copy.MENU_BODY.format(name=ctx.user.name or "there")
        if preface:
            body = f"{preface}\n\n{body}"
        entries = [e for mod in self.modules for e in mod.entries]
        if len(entries) <= m.BUTTONS_MAX:
            ctx.buttons(body, [m.Button(f"menu:{e.id}", e.title) for e in entries],
                        header=copy.BRAND, kind="menu")
        else:
            rows = [m.Row(f"menu:{e.id}", e.title, e.description) for e in entries[:m.LIST_ROWS_MAX]]
            ctx.list(body, copy.MENU_LIST_BUTTON, rows, section_title=copy.BRAND, header=copy.BRAND,
                     kind="menu")

    # --------------------------------------------------------- notifications

    @staticmethod
    def set_notifications(ctx: Ctx, on: bool) -> None:
        ctx.user.notifications_opt_in = on
        if on:
            ctx.user.opted_in_at = utcnow()

    def _opt_out(self, ctx: Ctx) -> None:
        ctx.user.notifications_opt_in = False
        if ctx.user.status != UserStatus.active:
            ctx.user.status = UserStatus.active  # STOP mid-onboarding still leaves a usable account
        ctx.set_state(MENU)
        ctx.buttons(copy.OPT_OUT, [m.Button("notify:on", copy.BTN_NOTIFY_ON), m.Button("menu:main", copy.BTN_MENU)],
                    kind="opt_out")


def build_bot(notifier: Notifier, settings: Settings) -> Bot:
    """The production module line-up. Add new modules here."""
    from .modules.contact import ContactModule
    from .modules.faq import FaqModule
    from .modules.jobs import JobsModule
    from .modules.settings import SettingsModule

    return Bot(notifier=notifier, settings=settings,
               modules=[JobsModule(), FaqModule.from_file(settings.faq_file), ContactModule(), SettingsModule()])
