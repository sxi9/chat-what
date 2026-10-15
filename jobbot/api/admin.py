"""Recruiter/operator side. Everything the WhatsApp side reads is written here."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Application, ApplicationStatus, Job, SupportTicket, TicketStatus, User
from ..services import applications as apps
from ..services.alerts import run_alerts
from ..services.notify import Notifier
from ..config import Settings, get_settings
from .deps import get_notifier, require_admin

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class JobIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    location: str = ""
    description: str = ""
    tags: str = ""
    salary: str = ""
    url: str = ""
    is_active: bool = True


class JobPatch(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    tags: str | None = None
    salary: str | None = None
    url: str | None = None
    is_active: bool | None = None


class JobOut(JobIn):
    id: int
    posted_at: datetime
    applications: int = 0

    model_config = {"from_attributes": True}


class ApplicationOut(BaseModel):
    id: int
    user_id: int
    phone: str
    applicant: str | None
    job_id: int
    job_title: str
    company: str
    status: ApplicationStatus
    note: str
    created_at: datetime
    updated_at: datetime


class ApplicationPatch(BaseModel):
    status: ApplicationStatus
    note: str = ""
    notify: bool = True


class UserOut(BaseModel):
    id: int
    phone: str
    name: str | None
    keywords: str
    location: str
    status: str
    notifications_opt_in: bool
    created_at: datetime
    last_inbound_at: datetime | None

    model_config = {"from_attributes": True}


def _job_out(job: Job, count: int) -> JobOut:
    return JobOut(id=job.id, title=job.title, company=job.company, location=job.location,
                  description=job.description, tags=job.tags, salary=job.salary, url=job.url,
                  is_active=job.is_active, posted_at=job.posted_at, applications=count)


def _app_out(a: Application) -> ApplicationOut:
    return ApplicationOut(id=a.id, user_id=a.user_id, phone=a.user.phone, applicant=a.user.name,
                          job_id=a.job_id, job_title=a.job.title, company=a.job.company, status=a.status,
                          note=a.note, created_at=a.created_at, updated_at=a.updated_at)


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobIn, db: Session = Depends(get_db)):
    job = Job(**body.model_dump())
    db.add(job)
    db.flush()
    return _job_out(job, 0)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(active_only: bool = False, db: Session = Depends(get_db)):
    q = select(Job, func.count(Application.id)).outerjoin(Application).group_by(Job.id).order_by(Job.posted_at.desc())
    if active_only:
        q = q.where(Job.is_active.is_(True))
    return [_job_out(job, count) for job, count in db.execute(q).all()]


@router.patch("/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: int, body: JobPatch, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(job, k, v)
    count = db.scalar(select(func.count(Application.id)).where(Application.job_id == job.id)) or 0
    return _job_out(job, count)


@router.get("/applications", response_model=list[ApplicationOut])
def list_applications(job_id: int | None = None, status: ApplicationStatus | None = None,
                      db: Session = Depends(get_db)):
    q = select(Application).order_by(Application.updated_at.desc())
    if job_id is not None:
        q = q.where(Application.job_id == job_id)
    if status is not None:
        q = q.where(Application.status == status)
    return [_app_out(a) for a in db.scalars(q).all()]


@router.patch("/applications/{app_id}", response_model=ApplicationOut)
def update_application(app_id: int, body: ApplicationPatch, db: Session = Depends(get_db),
                       notifier: Notifier = Depends(get_notifier)):
    """Recruiter moves an application. The applicant is told on WhatsApp."""
    app = db.get(Application, app_id)
    if app is None:
        raise HTTPException(404, "application not found")
    changed = apps.set_status(db, app, body.status, body.note)
    db.flush()
    if changed and body.notify and app.user.notifications_opt_in:
        notifier.status_update(db, app)
    return _app_out(app)


@router.get("/users", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.created_at.desc())).all()


@router.post("/alerts/run")
def trigger_alerts(db: Session = Depends(get_db), notifier: Notifier = Depends(get_notifier),
                   settings: Settings = Depends(get_settings)):
    """Same as `jobbot send-alerts`, for schedulers that can only call HTTP."""
    report = run_alerts(db, notifier, settings.alerts_per_user_per_run, settings.alert_lookback_days)
    return report.__dict__


class TicketOut(BaseModel):
    id: int
    user_id: int
    phone: str
    name: str | None
    message: str
    reply: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime


class TicketReply(BaseModel):
    reply: str = Field(min_length=1, max_length=4000)
    close: bool = False


def _ticket_out(t: SupportTicket) -> TicketOut:
    return TicketOut(id=t.id, user_id=t.user_id, phone=t.user.phone, name=t.user.name, message=t.message,
                     reply=t.reply, status=t.status, created_at=t.created_at, updated_at=t.updated_at)


@router.get("/tickets", response_model=list[TicketOut])
def list_tickets(status: TicketStatus | None = None, db: Session = Depends(get_db)):
    q = select(SupportTicket).order_by(SupportTicket.created_at.desc())
    if status is not None:
        q = q.where(SupportTicket.status == status)
    return [_ticket_out(t) for t in db.scalars(q).all()]


@router.post("/tickets/{ticket_id}/reply", response_model=TicketOut)
def reply_ticket(ticket_id: int, body: TicketReply, db: Session = Depends(get_db),
                 notifier: Notifier = Depends(get_notifier)):
    """The team answers; the user gets it on WhatsApp regardless of notification opt-in,
    because they asked for this reply."""
    t = db.get(SupportTicket, ticket_id)
    if t is None:
        raise HTTPException(404, "ticket not found")
    t.reply = body.reply
    t.status = TicketStatus.closed if body.close else TicketStatus.answered
    db.flush()
    notifier.ticket_reply(db, t)
    return _ticket_out(t)
