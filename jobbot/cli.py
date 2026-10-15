"""Command line: database setup, demo data, the alert cron job, and a local chat simulator."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

from .api.deps import set_sender
from .bot.engine import build_bot
from .config import get_settings
from .db import init_db, session_scope
from .models import Job, utcnow
from .services.alerts import run_alerts
from .services.reminders import run_reminders
from .services.notify import Notifier
from .whatsapp.client import RecordingSender
from .whatsapp.webhook import Inbound

DEMO_JOBS = [
    dict(title="Delivery Driver", company="QuickShip Logistics", location="Nairobi", salary="KES 35,000/month",
         tags="driver, delivery, logistics", description="Deliver parcels across Nairobi on a company motorbike. Valid licence required. Six days a week."),
    dict(title="Junior Accountant", company="Mwangi & Partners", location="Nairobi", salary="KES 60,000/month",
         tags="accountant, finance, bookkeeping", description="Prepare ledgers, reconcile accounts, support month-end close. CPA Part 2 or above."),
    dict(title="Customer Support Agent", company="Safiri Travel", location="Remote", salary="KES 40,000/month",
         tags="customer service, support, call centre", description="Answer customer questions over WhatsApp and phone. Evening shifts available. Work from home."),
    dict(title="Warehouse Assistant", company="QuickShip Logistics", location="Mombasa", salary="KES 28,000/month",
         tags="warehouse, logistics, packing", description="Sort and pack parcels at the Mombasa depot. Physically demanding, no experience needed."),
    dict(title="Sales Executive", company="BrightSolar Kenya", location="Nakuru", salary="Commission + KES 25,000 retainer",
         tags="sales, field sales, solar", description="Sell home solar kits to households in Nakuru county. Motorbike provided."),
    dict(title="Python Developer", company="Fintech Labs", location="Nairobi", salary="KES 180,000/month",
         tags="python, developer, software, backend", description="Build payment APIs with FastAPI and PostgreSQL. Two years' experience."),
    dict(title="Finance Manager", company="BrightSolar Kenya", location="Nakuru", salary="KES 150,000/month",
         tags="finance, accountant, management", description="Lead a team of three accountants. CPA(K) required."),
    dict(title="Taxi Driver", company="RideNow", location="Mombasa", salary="Per trip",
         tags="driver, taxi, transport", description="Drive for the RideNow app with your own vehicle or a leased one."),
]


def cmd_init_db(_: argparse.Namespace) -> None:
    init_db()
    print("database ready")


def cmd_seed(_: argparse.Namespace) -> None:
    init_db()
    with session_scope() as db:
        if db.query(Job).count():
            print("jobs already present, not seeding")
            return
        now = utcnow()
        for i, j in enumerate(DEMO_JOBS):
            db.add(Job(posted_at=now - timedelta(days=i % 4), **j))
    print(f"seeded {len(DEMO_JOBS)} demo jobs")


def _notifier() -> Notifier:
    from .api.deps import get_notifier
    return get_notifier()


def cmd_send_alerts(args: argparse.Namespace) -> None:
    settings = get_settings()
    init_db()
    if args.dry_run:
        set_sender(RecordingSender())
    with session_scope() as db:
        report = run_alerts(db, _notifier(), settings.alerts_per_user_per_run, settings.alert_lookback_days)
    print(json.dumps(report.__dict__, indent=2))


def cmd_send_reminders(args: argparse.Namespace) -> None:
    init_db()
    if args.dry_run:
        set_sender(RecordingSender())
    with session_scope() as db:
        report = run_reminders(db, _notifier())
    print(json.dumps(report.__dict__, indent=2))


def _describe(payload: dict) -> str:
    t = payload["type"]
    if t == "text":
        return payload["text"]["body"]
    if t == "template":
        tpl = payload["template"]
        params = [p["text"] for c in tpl["components"] for p in c.get("parameters", [])]
        return f"[template {tpl['name']}] " + " | ".join(params)
    inter = payload["interactive"]
    out = []
    if "header" in inter:
        out.append(f"== {inter['header']['text']} ==")
    out.append(inter["body"]["text"])
    if inter["type"] == "button":
        out.append("  " + "   ".join(f"[{b['reply']['title']}] ({b['reply']['id']})" for b in inter["action"]["buttons"]))
    else:
        out.append(f"  <{inter['action']['button']}>")
        for row in inter["action"]["sections"][0]["rows"]:
            out.append(f"   - {row['title']} ({row['id']})" + (f" — {row['description']}" if row.get("description") else ""))
    if "footer" in inter:
        out.append(f"  _{inter['footer']['text']}_")
    return "\n".join(out)


def cmd_chat(args: argparse.Namespace) -> None:
    """Talk to the bot in the terminal. Type text, or a button/row id such as menu:jobs or job:3."""
    init_db()
    sender = RecordingSender()
    settings = get_settings()
    agent = None
    if args.fake_assistant or not settings.assistant_on:
        from .assistant.agent import FakeAgent
        from .bot.modules.faq import FaqModule
        agent = FakeAgent(settings, FaqModule.from_file(settings.faq_file).items)
        print("Assistant: FAKE (set ANTHROPIC_API_KEY for the real one)")
    else:
        print(f"Assistant: Claude ({settings.assistant_model})")
    bot = build_bot(Notifier(sender=sender, settings=settings), settings, agent=agent)
    phone = args.phone
    n = 0
    print(f"Simulating {phone}. Type a message, or an id like menu:jobs / job:1 / apply:1. Ctrl-D to quit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except EOFError:
            print()
            return
        if not text:
            continue
        n += 1
        kind = "button" if ":" in text and " " not in text else "text"
        msg = Inbound(wa_message_id=f"sim.{phone}.{n}", phone=phone, kind=kind, text=text)
        with session_scope() as db:
            bot.handle(db, msg)
        for payload in sender.sent:
            print("bot> " + _describe(payload).replace("\n", "\n     "))
        sender.clear()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jobbot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="create tables").set_defaults(fn=cmd_init_db)
    sub.add_parser("seed", help="insert demo jobs").set_defaults(fn=cmd_seed)
    a = sub.add_parser("send-alerts", help="send job alerts to opted-in users (run from cron)")
    a.add_argument("--dry-run", action="store_true", help="compute and print, send nothing")
    a.set_defaults(fn=cmd_send_alerts)
    c = sub.add_parser("chat", help="local terminal simulator, no WhatsApp needed")
    c.add_argument("--phone", default="15550001111")
    c.add_argument("--fake-assistant", action="store_true", help="use the offline stand-in even if a key is set")
    c.set_defaults(fn=cmd_chat)
    r = sub.add_parser("send-reminders", help="deliver due reminders the assistant scheduled (run from cron)")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(fn=cmd_send_reminders)
    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main(sys.argv[1:])
