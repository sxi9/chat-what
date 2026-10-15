"""Deliver reminders the assistant scheduled. Run `jobbot send-reminders` from cron."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Reminder, UserStatus, utcnow
from .notify import Notifier


@dataclass
class ReminderRunReport:
    due: int = 0
    sent: int = 0
    skipped_opt_out: int = 0
    failures: int = 0
    details: list[str] = field(default_factory=list)


def run_reminders(db: Session, notifier: Notifier) -> ReminderRunReport:
    report = ReminderRunReport()
    due = db.scalars(select(Reminder).where(Reminder.sent_at.is_(None), Reminder.due_at <= utcnow())).all()
    for r in due:
        report.due += 1
        if not r.user.notifications_opt_in or r.user.status != UserStatus.active:
            r.sent_at = utcnow()  # drop it; the user asked for silence
            report.skipped_opt_out += 1
            continue
        result = notifier.reminder(db, r)
        if result.ok:
            r.sent_at = utcnow()
            report.sent += 1
        else:
            report.failures += 1
            report.details.append(f"{r.user.phone}: {result.error}")
    return report
