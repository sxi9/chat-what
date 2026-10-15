import os

# Settings are cached at import time, so the test environment must be set before any jobbot import.
os.environ.update({
    "DATABASE_URL": "sqlite:///:memory:",
    "ADMIN_API_KEY": "test-admin-key",
    "WA_APP_SECRET": "test-app-secret",
    "WA_VERIFY_TOKEN": "test-verify-token",
    "WA_ACCESS_TOKEN": "",
    "WA_PHONE_NUMBER_ID": "",
    "RECOMMENDATIONS_LIMIT": "3",
})

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from jobbot import db as dbmod  # noqa: E402
from jobbot.api.deps import get_bot, set_sender  # noqa: E402
from jobbot.config import get_settings  # noqa: E402
from jobbot.main import app  # noqa: E402
from jobbot.models import Job, utcnow  # noqa: E402
from jobbot.whatsapp.client import RecordingSender  # noqa: E402
from jobbot.whatsapp.webhook import Inbound  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    dbmod.configure_engine("sqlite:///:memory:")
    dbmod.init_db()
    yield


@pytest.fixture
def sender():
    s = RecordingSender()
    set_sender(s)
    yield s
    set_sender(None)


@pytest.fixture
def bot(sender):
    return get_bot()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client(sender):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seed_jobs():
    now = utcnow()
    jobs = [
        Job(title="Delivery Driver", company="QuickShip", location="Nairobi", tags="driver, delivery",
            description="Deliver parcels on a motorbike.", posted_at=now),
        Job(title="Junior Accountant", company="Mwangi & Partners", location="Nairobi", tags="accountant, finance",
            description="Ledgers and reconciliations.", posted_at=now - timedelta(days=1)),
        Job(title="Taxi Driver", company="RideNow", location="Mombasa", tags="driver, taxi",
            description="Drive for the app.", posted_at=now - timedelta(days=2)),
        Job(title="Support Agent", company="Safiri", location="Remote", tags="support, customer service",
            description="Answer customers on WhatsApp.", posted_at=now - timedelta(days=5)),
        Job(title="Old Driver Job", company="Legacy Co", location="Nairobi", tags="driver",
            description="Inactive posting.", posted_at=now - timedelta(days=30), is_active=False),
    ]
    with dbmod.session_scope() as db:
        db.add_all(jobs)
        db.flush()
        return [j.id for j in jobs]


class Chat:
    """Drive the bot as a single WhatsApp user and inspect what it sent back."""

    def __init__(self, bot, sender, phone="15550001111"):
        self.bot, self.sender, self.phone, self.n = bot, sender, phone, 0

    def _send(self, kind, text, label=""):
        self.n += 1
        self.sender.clear()
        msg = Inbound(wa_message_id=f"wamid.{self.phone}.{self.n}", phone=self.phone, kind=kind, text=text, label=label)
        with dbmod.session_scope() as db:
            handled = self.bot.handle(db, msg)
        return handled

    def say(self, text):
        self._send("text", text)
        return self.replies()

    def tap(self, button_id):
        self._send("button", button_id)
        return self.replies()

    def pick(self, row_id):
        self._send("list", row_id)
        return self.replies()

    def replies(self):
        return [Reply(p) for p in self.sender.sent]

    def last(self):
        return self.replies()[-1]


class Reply:
    def __init__(self, payload):
        self.payload = payload
        self.type = payload["type"]

    @property
    def body(self):
        if self.type == "text":
            return self.payload["text"]["body"]
        if self.type == "template":
            return self.payload["template"]["name"]
        return self.payload["interactive"]["body"]["text"]

    @property
    def interactive_type(self):
        return self.payload.get("interactive", {}).get("type")

    @property
    def button_ids(self):
        if self.interactive_type != "button":
            return []
        return [b["reply"]["id"] for b in self.payload["interactive"]["action"]["buttons"]]

    @property
    def button_titles(self):
        return [b["reply"]["title"] for b in self.payload["interactive"]["action"]["buttons"]]

    @property
    def row_ids(self):
        if self.interactive_type != "list":
            return []
        return [r["id"] for s in self.payload["interactive"]["action"]["sections"] for r in s["rows"]]

    @property
    def template_params(self):
        return [p["text"] for c in self.payload["template"]["components"] for p in c["parameters"]]


@pytest.fixture
def chat(bot, sender):
    return Chat(bot, sender)


@pytest.fixture
def onboarded(chat, seed_jobs):
    """A user who finished onboarding with notifications on and job preferences set."""
    chat.say("hi")
    chat.say("Asha")
    chat.tap("notify:on")
    chat.pick("menu:jobs")
    chat.say("driver, delivery")
    chat.say("Nairobi")
    chat.sender.clear()
    return chat
