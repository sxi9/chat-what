"""Outbound messaging that respects the 24-hour service window.

Inside the window we may send anything. Outside it WhatsApp only accepts
pre-approved templates, so each notification has a template fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import Application, Job, OutboundMessage, Reminder, SupportTicket, User
from ..whatsapp import messages as m
from ..whatsapp.client import SendResult, Sender
from . import applications as apps

log = logging.getLogger(__name__)


@dataclass
class Notifier:
    sender: Sender
    settings: Settings

    def _log(self, db: Session, user: User | None, kind: str, result: SendResult) -> None:
        db.add(
            OutboundMessage(
                user_id=user.id if user else None,
                wa_message_id=result.wa_message_id,
                kind=kind,
                delivery_status="sent" if result.ok else "failed",
                error=result.error,
            )
        )

    def send(self, db: Session, user: User | None, kind: str, payload: dict) -> SendResult:
        result = self.sender.send(payload)
        self._log(db, user, kind, result)
        return result

    # ----- job alerts -------------------------------------------------------

    def job_alert(self, db: Session, user: User, jobs: list[Job]) -> SendResult:
        if user.in_service_window():
            rows = [m.Row(f"job:{j.id}", j.title, f"{j.company} · {j.location}") for j in jobs]
            body = (f"Hi {user.name or 'there'}, {len(jobs)} new job{'s' if len(jobs) != 1 else ''} "
                    f"match your search. Tap to see details and apply.")
            payload = m.list_message(user.phone, body, "View jobs", rows, section_title="New matches",
                                     footer="Reply STOP to pause alerts")
            return self.send(db, user, "job_alert_list", payload)
        # Outside the window: one template per job (template body has fixed slots).
        last = SendResult(ok=False, error="no jobs")
        for j in jobs:
            payload = m.template_message(
                user.phone,
                self.settings.wa_template_job_alert,
                self.settings.wa_template_language,
                [user.name or "there", j.title, j.company, j.location or "Not stated", f"J{j.id}"],
            )
            last = self.send(db, user, "job_alert_template", payload)
        return last

    # ----- application status ----------------------------------------------

    def status_update(self, db: Session, app: Application) -> SendResult:
        user = app.user
        job = app.job
        label = apps.status_label(app.status)
        if user.in_service_window():
            body = (f"Update on your application for *{job.title}* at {job.company}:\n\n"
                    f"Status: *{label}*\n{apps.next_step(app.status)}")
            payload = m.button_message(user.phone, body, [m.Button("menu:status", "My applications"),
                                                         m.Button("menu:main", "Main menu")])
            return self.send(db, user, "status_update_text", payload)
        payload = m.template_message(
            user.phone,
            self.settings.wa_template_status_update,
            self.settings.wa_template_language,
            [user.name or "there", job.title, job.company, label],
        )
        return self.send(db, user, "status_update_template", payload)

    # ----- support tickets -------------------------------------------------

    def ticket_reply(self, db: Session, ticket: SupportTicket) -> SendResult:
        user = ticket.user
        if user.in_service_window():
            body = f"Reply from our team (ticket T{ticket.id}):\n\n{ticket.reply}"
            payload = m.button_message(user.phone, body, [m.Button("menu:contact", "Reply"),
                                                         m.Button("menu:main", "Main menu")])
            return self.send(db, user, "ticket_reply_text", payload)
        payload = m.template_message(
            user.phone,
            self.settings.wa_template_ticket_reply,
            self.settings.wa_template_language,
            [user.name or "there", f"T{ticket.id}", m.clip(ticket.reply, 900)],
        )
        return self.send(db, user, "ticket_reply_template", payload)

    # ----- reminders --------------------------------------------------------

    def reminder(self, db: Session, r: Reminder) -> SendResult:
        user = r.user
        if user.in_service_window():
            payload = m.button_message(user.phone, r.message, [m.Button("menu:assistant", "Reply"),
                                                               m.Button("menu:main", "Main menu")])
            return self.send(db, user, "reminder_text", payload)
        payload = m.template_message(user.phone, self.settings.wa_template_reminder,
                                     self.settings.wa_template_language,
                                     [user.name or "there", m.clip(r.message, 900)])
        return self.send(db, user, "reminder_template", payload)
