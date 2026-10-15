"""The assistant module with a fake brain: routing, confirmations, memory, reminders, tools."""
import json
from datetime import timedelta

import pytest

from jobbot import db as dbmod
from jobbot.api.deps import get_bot, get_notifier, set_agent
from jobbot.assistant import memory
from jobbot.assistant.agent import FakeAgent
from jobbot.assistant.tools import Side, build_tools
from jobbot.bot.modules.assistant import _split
from jobbot.bot.modules.faq import FaqModule
from jobbot.config import get_settings
from jobbot.models import Application, ApplicationStatus, PendingAction, PendingActionStatus, Reminder, User, utcnow
from jobbot.services.reminders import run_reminders
from tests.conftest import Chat

H = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
def fake_agent(sender):
    settings = get_settings()
    agent = FakeAgent(settings, FaqModule.from_file(settings.faq_file).items)
    set_agent(agent)
    yield agent
    set_agent(None)


@pytest.fixture
def achat(fake_agent, sender, seed_jobs):
    chat = Chat(get_bot(), sender)
    chat.say("hi"); chat.say("Asha"); chat.tap("notify:on")
    chat.sender.clear()
    return chat


def test_menu_gains_ask_anything_first(achat):
    (menu,) = achat.say("menu")
    assert menu.row_ids[0] == "menu:assistant"


def test_unknown_text_goes_to_assistant_not_menu(achat, fake_agent):
    (reply,) = achat.say("what should I do with my life")
    assert "[fake assistant]" in reply.body
    assert fake_agent.calls == ["what should I do with my life"]


def test_shortcuts_and_stop_still_deterministic(achat, fake_agent):
    assert "What kind of work" in achat.say("jobs")[-1].body  # deterministic jobs module, not the model
    assert "paused" in achat.say("STOP")[-1].body
    assert fake_agent.calls == []


def test_chat_state_keeps_routing_to_assistant_until_menu(achat, fake_agent):
    (intro,) = achat.pick("menu:assistant")
    assert "own words" in intro.body
    achat.say("jobs")  # in chat state even a shortcut word goes to the assistant
    assert fake_agent.calls == ["jobs"]
    (menu,) = achat.say("menu")
    assert menu.interactive_type == "list"
    achat.say("jobs")
    assert fake_agent.calls == ["jobs"]  # back in menu state the shortcut is deterministic again


def test_apply_requires_confirmation_then_executes(achat, seed_jobs):
    (proposal,) = achat.say(f"apply {seed_jobs[0]}")
    assert proposal.button_ids == ["confirm:1", "cancel:1"]
    with dbmod.session_scope() as db:
        assert db.query(Application).count() == 0
        assert db.query(PendingAction).one().status == PendingActionStatus.pending
    (done,) = achat.tap("confirm:1")
    assert "You've applied" in done.body
    with dbmod.session_scope() as db:
        assert db.query(Application).one().job_id == seed_jobs[0]
        assert db.query(PendingAction).one().status == PendingActionStatus.confirmed
    (again,) = achat.tap("confirm:1")
    assert "no longer pending" in again.body


def test_cancel_leaves_everything_unchanged(achat, seed_jobs):
    achat.say(f"apply {seed_jobs[0]}")
    (reply,) = achat.tap("cancel:1")
    assert "Cancelled" in reply.body
    with dbmod.session_scope() as db:
        assert db.query(Application).count() == 0


def test_new_proposal_supersedes_old_one(achat, seed_jobs):
    achat.say(f"apply {seed_jobs[0]}")
    achat.say(f"apply {seed_jobs[1]}")
    with dbmod.session_scope() as db:
        statuses = {p.id: p.status for p in db.query(PendingAction).all()}
        assert statuses == {1: PendingActionStatus.cancelled, 2: PendingActionStatus.pending}
    assert "no longer pending" in achat.tap("confirm:1")[0].body


def test_withdraw_via_assistant(achat, seed_jobs):
    achat.say(f"apply {seed_jobs[0]}"); achat.tap("confirm:1")
    with dbmod.session_scope() as db:
        app_id = db.query(Application).one().id
    achat.say(f"withdraw {app_id}")
    (done,) = achat.tap("confirm:2")
    assert "withdrawn" in done.body
    with dbmod.session_scope() as db:
        assert db.query(Application).one().status == ApplicationStatus.withdrawn


def test_other_user_cannot_confirm_my_action(achat, fake_agent, sender, seed_jobs):
    achat.say(f"apply {seed_jobs[0]}")
    other = Chat(get_bot(), sender, "15550009999")
    other.say("hi"); other.say("Mallory"); other.tap("notify:on")
    (reply,) = other.tap("confirm:1")
    assert "no longer pending" in reply.body
    with dbmod.session_scope() as db:
        assert db.query(Application).count() == 0


def test_memory_and_transcript_persist(achat):
    achat.say("remember I have a class B licence")
    with dbmod.session_scope() as db:
        u = db.query(User).filter_by(phone=achat.phone).one()
        assert [m.value for m in memory.memories(db, u)] == ["I have a class B licence"]
        history = memory.recent_messages(db, u, 20)
        assert [h["role"] for h in history] == ["user", "assistant"]
        assert history[0]["content"].startswith("remember")


def test_recent_messages_merges_runs_and_starts_with_user():
    with dbmod.session_scope() as db:
        u = User(phone="7"); db.add(u); db.flush()
        memory.add_message(db, u, "assistant", "orphan")
        memory.add_message(db, u, "user", "a")
        memory.add_message(db, u, "user", "b")
        memory.add_message(db, u, "assistant", "c")
        hist = memory.recent_messages(db, u, 10)
        assert hist == [{"role": "user", "content": "a\nb"}, {"role": "assistant", "content": "c"}]


def test_reminder_scheduled_and_delivered(achat, sender):
    achat.say("remind me tomorrow")
    with dbmod.session_scope() as db:
        r = db.query(Reminder).one()
        assert r.sent_at is None and r.due_at > utcnow() + timedelta(hours=23)
        report = run_reminders(db, get_notifier())
        assert report.due == 0  # not due yet
        r.due_at = utcnow() - timedelta(minutes=1)
    sender.clear()
    with dbmod.session_scope() as db:
        report = run_reminders(db, get_notifier())
        assert report.sent == 1
        assert db.query(Reminder).one().sent_at is not None
    assert sender.sent[-1]["type"] == "interactive"  # user is inside the 24h window
    assert "check in" in sender.sent[-1]["interactive"]["body"]["text"]


def test_reminder_dropped_when_user_opted_out(achat, sender):
    achat.say("remind me tomorrow")
    achat.say("STOP")
    with dbmod.session_scope() as db:
        db.query(Reminder).one().due_at = utcnow() - timedelta(minutes=1)
    sender.clear()
    with dbmod.session_scope() as db:
        report = run_reminders(db, get_notifier())
    assert report.skipped_opt_out == 1 and sender.sent == []


def test_tools_directly(seed_jobs):
    settings = get_settings()
    faq = FaqModule.from_file(settings.faq_file).items
    with dbmod.session_scope() as db:
        u = User(phone="8", name="Asha", keywords="driver", location="Nairobi"); db.add(u); db.flush()
        side = Side()
        t = {x.name: x for x in build_tools(db, u, side, settings, faq)}
        res = json.loads(t["search_jobs"].call({}))
        assert res["results"][0]["title"] == "Delivery Driver"
        res = json.loads(t["search_jobs"].call({"query": "accountant", "location": "any"}))
        assert res["results"][0]["title"] == "Junior Accountant"
        detail = json.loads(t["get_job"].call({"job_id": seed_jobs[0]}))
        assert detail["already_applied"] is False and "motorbike" in detail["description"]
        assert "error" in json.loads(t["get_job"].call({"job_id": seed_jobs[4]}))  # inactive
        assert "CONFIRMATION REQUIRED" in t["apply_to_job"].call({"job_id": seed_jobs[0]})
        assert side.pending is not None and side.pending.kind == "apply"
        prefs = json.loads(t["update_preferences"].call({"location": "Mombasa"}))
        assert prefs == {"keywords": "driver", "location": "Mombasa"}
        assert json.loads(t["set_notifications"].call({"enabled": True}))["notifications"] == "on"
        faq_hits = json.loads(t["faq_lookup"].call({"query": "free"}))
        assert any("never pay" in h["answer"] for h in faq_hits)
        ticket = json.loads(t["contact_team"].call({"message": "Delete my data"}))
        assert ticket["ticket_id"] == 1
        rem = json.loads(t["schedule_reminder"].call({"hours_from_now": 0.1, "message": "hi"}))
        assert rem["reminder_id"] == 1  # clamped to the 1-hour minimum
        assert db.query(Reminder).one().due_at > utcnow() + timedelta(minutes=55)
        t["remember"].call({"key": "Licence", "value": "Class B"})
        assert memory.memories(db, u)[0].key == "licence"
        assert json.loads(t["forget"].call({"key": "licence"}))["forgotten"] is True
        assert "UTC" in t["current_time"].call({})


def test_tool_schemas_are_strict_json_schema(seed_jobs):
    settings = get_settings()
    with dbmod.session_scope() as db:
        u = User(phone="9"); db.add(u); db.flush()
        for tool in build_tools(db, u, Side(), settings, []):
            d = tool.to_dict()
            assert d["name"] and d["description"]
            assert d["input_schema"]["type"] == "object"


def test_split_long_replies():
    assert _split("short") == ["short"]
    parts = _split("\n".join(["x" * 1000] * 10), limit=3900)
    assert all(len(p) <= 3900 for p in parts) and len(parts) == 4
    assert _split("y" * 5000, limit=3900) == ["y" * 3900, "y" * 1100]


def test_claude_agent_handles_api_errors_gracefully(seed_jobs):
    """Wire ClaudeAgent to a client whose runner raises, and check the user gets a sane reply."""
    import anthropic
    from jobbot.assistant.agent import ClaudeAgent

    class Boom:
        class beta:
            class messages:
                @staticmethod
                def tool_runner(**kw):
                    raise anthropic.APIConnectionError(request=None)

    settings = get_settings()
    agent = ClaudeAgent(settings, [], client=Boom())
    with dbmod.session_scope() as db:
        u = User(phone="10", name="Asha"); db.add(u); db.flush()
        reply = agent.reply(db, u, "hello")
    assert "MENU" in reply.text and reply.pending is None


def test_claude_agent_builds_request_and_reads_reply(seed_jobs):
    """A scripted client: assert the request shape the skill prescribes, and the reply handling."""
    from jobbot.assistant.agent import ClaudeAgent

    captured = {}

    class Block:
        def __init__(self, type, text=""):
            self.type, self.text = type, text

    class Final:
        stop_reason = "end_turn"
        content = [Block("thinking"), Block("text", "Hello Asha, I found "), Block("text", "two jobs.")]

    class Runner:
        def until_done(self):
            return Final()

    class Client:
        class beta:
            class messages:
                @staticmethod
                def tool_runner(**kw):
                    captured.update(kw)
                    return Runner()

    settings = get_settings()
    agent = ClaudeAgent(settings, [], client=Client())
    with dbmod.session_scope() as db:
        u = User(phone="11", name="Asha", keywords="driver"); db.add(u); db.flush()
        memory.remember(db, u, "licence", "class B")
        reply = agent.reply(db, u, "any jobs?")
        assert reply.text == "Hello Asha, I found two jobs."
        assert [h["role"] for h in memory.recent_messages(db, u, 10)] == ["user", "assistant"]
    assert captured["model"] == settings.assistant_model
    assert captured["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in captured["betas"]
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "licence: class B" in captured["system"][1]["text"]
    assert captured["messages"][-1] == {"role": "user", "content": "any jobs?"}
    names = [t.name for t in captured["tools"] if hasattr(t, "name")]
    assert "apply_to_job" in names and "schedule_reminder" in names
    assert any(isinstance(t, dict) and t["type"].startswith("web_search") for t in captured["tools"])
    assert "thinking" not in captured  # adaptive by default on this model
