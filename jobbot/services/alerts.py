"""Scheduled job alerts. Run `jobbot send-alerts` from cron."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AlertSent, User, UserStatus
from . import jobs as jobsvc
from .notify import Notifier

log = logging.getLogger(__name__)


@dataclass
class AlertRunReport:
    users_considered: int = 0
    users_alerted: int = 0
    jobs_sent: int = 0
    failures: int = 0
    details: list[str] = field(default_factory=list)


def alerted_job_ids(db: Session, user: User) -> set[int]:
    return {r[0] for r in db.execute(select(AlertSent.job_id).where(AlertSent.user_id == user.id)).all()}


def run_alerts(db: Session, notifier: Notifier, per_user: int, lookback_days: int) -> AlertRunReport:
    report = AlertRunReport()
    users = db.scalars(
        select(User).where(User.status == UserStatus.active, User.notifications_opt_in.is_(True))
    ).all()
    for user in users:
        report.users_considered += 1
        if not user.keyword_list():
            continue
        exclude = alerted_job_ids(db, user)
        jobs = jobsvc.recommend(db, user, limit=per_user, exclude=exclude, max_age_days=lookback_days)
        if not jobs:
            continue
        result = notifier.job_alert(db, user, jobs)
        if not result.ok:
            report.failures += 1
            report.details.append(f"{user.phone}: {result.error}")
            continue
        for j in jobs:
            db.add(AlertSent(user_id=user.id, job_id=j.id))
        db.flush()
        report.users_alerted += 1
        report.jobs_sent += len(jobs)
    return report
