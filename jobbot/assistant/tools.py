"""Tools the assistant can call. Built per request so each closes over the db and the user.

Anything with a real side effect (apply, withdraw) does not run here. It records a
PendingAction and tells the model to ask the user, and the module renders Confirm/Cancel
buttons. The user's tap executes it deterministically.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta

from anthropic import beta_tool
from sqlalchemy.orm import Session

from ..bot.modules.faq import FaqItem
from ..config import Settings
from ..models import PendingAction, PendingActionStatus, Reminder, SupportTicket, TicketStatus, User, utcnow
from ..services import applications as apps
from ..services import jobs as jobsvc
from . import memory


@dataclass
class Side:
    """What the tools produced besides their text results."""

    pending: PendingAction | None = None
    reminders: list[Reminder] = field(default_factory=list)


def _job_dict(j) -> dict:
    return {"job_id": j.id, "title": j.title, "company": j.company, "location": j.location,
            "salary": j.salary, "posted": j.posted_at.strftime("%Y-%m-%d"), "tags": j.tags}


def _propose(db: Session, user: User, side: Side, kind: str, payload: dict, summary: str) -> str:
    for old in db.query(PendingAction).filter_by(user_id=user.id, status=PendingActionStatus.pending).all():
        old.status = PendingActionStatus.cancelled
    pa = PendingAction(user_id=user.id, kind=kind, payload=json.dumps(payload), summary=summary[:300])
    db.add(pa)
    db.flush()
    side.pending = pa
    return (f"CONFIRMATION REQUIRED. Nothing has been done yet. The user will see Confirm and Cancel "
            f"buttons under your reply. Briefly tell them what will happen if they confirm: {summary}")


def build_tools(db: Session, user: User, side: Side, settings: Settings, faq_items: list[FaqItem]) -> list:
    @beta_tool
    def search_jobs(query: str = "", location: str = "", limit: int = 5) -> str:
        """Search the job board. Use the user's own words; leave fields empty to use their saved preferences.

        Args:
            query: Keywords such as job title or skills, comma separated. Empty means the user's saved keywords.
            location: City or area. Empty means the user's saved location. "any" means anywhere.
            limit: How many jobs to return, 1 to 10.
        """
        probe = User(phone=user.phone, keywords=query or user.keywords, location=location or user.location)
        probe.id = user.id
        jobs = jobsvc.recommend(db, probe, limit=max(1, min(limit, 10)))
        if not jobs:
            jobs = jobsvc.latest_jobs(db, limit=max(1, min(limit, 10)))
            if not jobs:
                return json.dumps({"results": [], "note": "No jobs are posted yet."})
            return json.dumps({"results": [_job_dict(j) for j in jobs],
                               "note": "No jobs matched the query; these are the newest postings."})
        return json.dumps({"results": [_job_dict(j) for j in jobs]})

    @beta_tool
    def get_job(job_id: int) -> str:
        """Full details of one job, including the description and whether the user already applied.

        Args:
            job_id: The job's id from search_jobs.
        """
        j = jobsvc.get_job(db, job_id)
        if j is None or not j.is_active:
            return json.dumps({"error": "job not found or no longer open"})
        d = _job_dict(j)
        d["description"] = j.description
        d["url"] = j.url
        existing = next((a for a in user.applications if a.job_id == j.id), None)
        d["already_applied"] = bool(existing)
        if existing:
            d["application_id"] = existing.id
            d["application_status"] = apps.status_label(existing.status)
        return json.dumps(d)

    @beta_tool
    def apply_to_job(job_id: int) -> str:
        """Propose applying to a job on the user's behalf. Requires the user's confirmation via buttons.

        Args:
            job_id: The job's id.
        """
        j = jobsvc.get_job(db, job_id)
        if j is None or not j.is_active:
            return json.dumps({"error": "job not found or no longer open"})
        if any(a.job_id == j.id for a in user.applications):
            return json.dumps({"error": "already applied to this job"})
        return _propose(db, user, side, "apply", {"job_id": j.id}, f"apply for {j.title} at {j.company}")

    @beta_tool
    def my_applications() -> str:
        """List the user's job applications with their current status and the employer's note, newest first."""
        items = apps.list_for_user(db, user, limit=20)
        return json.dumps([{"application_id": a.id, "job_id": a.job_id, "title": a.job.title, "company": a.job.company,
                            "status": apps.status_label(a.status), "note": a.note,
                            "applied": a.created_at.strftime("%Y-%m-%d"),
                            "updated": a.updated_at.strftime("%Y-%m-%d"),
                            "what_next": apps.next_step(a.status)} for a in items])

    @beta_tool
    def withdraw_application(application_id: int) -> str:
        """Propose withdrawing one of the user's applications. Requires the user's confirmation via buttons.

        Args:
            application_id: The application's id from my_applications.
        """
        a = apps.get_for_user(db, user, application_id)
        if a is None:
            return json.dumps({"error": "application not found"})
        if a.status.value in ("withdrawn", "rejected"):
            return json.dumps({"error": "this application can no longer be withdrawn"})
        return _propose(db, user, side, "withdraw", {"application_id": a.id},
                        f"withdraw the application for {a.job.title} at {a.job.company}")

    @beta_tool
    def update_preferences(keywords: str = "", location: str = "") -> str:
        """Save the user's job search preferences. Only pass the fields the user wants to change.

        Args:
            keywords: Comma-separated job keywords, e.g. "driver, delivery".
            location: City or area, or "any".
        """
        if keywords.strip():
            user.keywords = ", ".join(k.strip() for k in keywords.split(",") if k.strip())[:500]
        if location.strip():
            user.location = location.strip()[:120]
        db.flush()
        return json.dumps({"keywords": user.keywords, "location": user.location})

    @beta_tool
    def set_notifications(enabled: bool) -> str:
        """Turn proactive notifications (job alerts, status updates, reminders) on or off for the user.

        Args:
            enabled: True to turn them on, False to pause them.
        """
        user.notifications_opt_in = enabled
        if enabled:
            user.opted_in_at = utcnow()
        db.flush()
        return json.dumps({"notifications": "on" if enabled else "off"})

    @beta_tool
    def remember(key: str, value: str) -> str:
        """Store a lasting fact about the user for future conversations, e.g. skills, availability, salary expectation.

        Args:
            key: Short snake_case label, e.g. "salary_expectation" or "has_driving_licence".
            value: The fact, in one sentence.
        """
        memory.remember(db, user, key, value)
        return json.dumps({"remembered": key})

    @beta_tool
    def forget(key: str) -> str:
        """Delete a stored fact about the user.

        Args:
            key: The label used when it was remembered.
        """
        return json.dumps({"forgotten": memory.forget(db, user, key)})

    @beta_tool
    def faq_lookup(query: str = "") -> str:
        """Look up the service's own help articles (how it works, cost, privacy, alerts, CVs).

        Args:
            query: Words to match; empty returns every article.
        """
        q = query.lower().split()
        hits = [i for i in faq_items if not q or any(w in (i.question + " " + i.answer).lower() for w in q)]
        return json.dumps([{"question": i.question, "answer": i.answer} for i in hits or faq_items])

    @beta_tool
    def contact_team(message: str) -> str:
        """Open a support ticket with the human team on the user's behalf. Use when a request needs a person.

        Args:
            message: What the user needs, in their words.
        """
        t = SupportTicket(user_id=user.id, message=message[:4000], status=TicketStatus.open)
        db.add(t)
        db.flush()
        return json.dumps({"ticket_id": t.id, "note": "The team will reply on WhatsApp."})

    @beta_tool
    def schedule_reminder(hours_from_now: float, message: str) -> str:
        """Schedule a WhatsApp message to the user for later, e.g. a follow-up you promised.

        Args:
            hours_from_now: Delay in hours, between 1 and 720.
            message: What to say when it fires, written to the user.
        """
        hours = max(1.0, min(float(hours_from_now), 720.0))
        r = Reminder(user_id=user.id, due_at=utcnow() + timedelta(hours=hours), message=message[:1500])
        db.add(r)
        db.flush()
        side.reminders.append(r)
        return json.dumps({"reminder_id": r.id, "due_at_utc": r.due_at.isoformat(timespec="minutes")})

    @beta_tool
    def current_time() -> str:
        """The current date and time in UTC."""
        return utcnow().strftime("%A %Y-%m-%d %H:%M UTC")

    return [search_jobs, get_job, apply_to_job, my_applications, withdraw_application, update_preferences,
            set_notifications, remember, forget, faq_lookup, contact_team, schedule_reminder, current_time]
