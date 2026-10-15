from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import STATUS_LABELS, Application, ApplicationStatus, Job, User, utcnow


def apply(db: Session, user: User, job: Job) -> tuple[Application, bool]:
    """Create an application. Returns (application, created)."""
    existing = db.scalar(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    )
    if existing:
        return existing, False
    app = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.applied)
    db.add(app)
    db.flush()
    return app, True


def list_for_user(db: Session, user: User, limit: int = 10) -> list[Application]:
    return db.scalars(
        select(Application)
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc(), Application.id.desc())
        .limit(limit)
    ).all()


def get_for_user(db: Session, user: User, app_id: int) -> Application | None:
    app = db.get(Application, app_id)
    if app is None or app.user_id != user.id:
        return None
    return app


def withdraw(db: Session, app: Application) -> bool:
    if app.status in (ApplicationStatus.withdrawn, ApplicationStatus.rejected):
        return False
    app.status = ApplicationStatus.withdrawn
    app.updated_at = utcnow()
    return True


def set_status(db: Session, app: Application, status: ApplicationStatus, note: str = "") -> bool:
    """Recruiter-side status change. Returns True if anything changed."""
    changed = app.status != status or (note and note != app.note)
    app.status = status
    if note:
        app.note = note
    app.updated_at = utcnow()
    return bool(changed)


def status_label(status: ApplicationStatus) -> str:
    return STATUS_LABELS[status]


def next_step(status: ApplicationStatus) -> str:
    return {
        ApplicationStatus.applied: "The employer has your application. We will message you when it moves.",
        ApplicationStatus.under_review: "A recruiter is reviewing your profile. No action needed yet.",
        ApplicationStatus.interview: "Expect the employer to contact you to schedule an interview.",
        ApplicationStatus.offer: "Congratulations. The employer will reach out with the details.",
        ApplicationStatus.rejected: "This one did not work out. Tap Jobs for new matches.",
        ApplicationStatus.withdrawn: "You withdrew this application.",
    }[status]


def format_application(app: Application) -> str:
    job = app.job
    lines = [
        f"*{job.title}*",
        f"{job.company} · {job.location or 'Location not stated'}",
        "",
        f"Status: *{status_label(app.status)}*",
        f"Applied: {app.created_at.strftime('%d %b %Y')}",
        f"Last update: {app.updated_at.strftime('%d %b %Y')}",
    ]
    if app.note:
        lines.append(f"Note from employer: {app.note}")
    lines += ["", next_step(app.status), "", f"Ref A{app.id}"]
    return "\n".join(lines)
