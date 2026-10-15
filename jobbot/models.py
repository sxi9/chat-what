from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class UserStatus(str, enum.Enum):
    onboarding = "onboarding"
    active = "active"
    blocked = "blocked"


class TicketStatus(str, enum.Enum):
    open = "open"
    answered = "answered"
    closed = "closed"


class ApplicationStatus(str, enum.Enum):
    applied = "applied"
    under_review = "under_review"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


STATUS_LABELS = {
    ApplicationStatus.applied: "Applied",
    ApplicationStatus.under_review: "Under review",
    ApplicationStatus.interview: "Interview stage",
    ApplicationStatus.offer: "Offer made",
    ApplicationStatus.rejected: "Not selected",
    ApplicationStatus.withdrawn: "Withdrawn",
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120))
    keywords: Mapped[str] = mapped_column(String(500), default="")
    location: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[UserStatus] = mapped_column(Enum(UserStatus), default=UserStatus.onboarding)
    notifications_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    opted_in_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    conversation: Mapped[ConversationState | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    applications: Mapped[list[Application]] = relationship(back_populates="user")

    def in_service_window(self, now: datetime | None = None) -> bool:
        """True while free-form messages are allowed (24h since the user's last message)."""
        if self.last_inbound_at is None:
            return False
        now = now or utcnow()
        return (now - self.last_inbound_at).total_seconds() < 24 * 3600

    def keyword_list(self) -> list[str]:
        return [k.strip().lower() for k in (self.keywords or "").split(",") if k.strip()]


class ConversationState(Base):
    __tablename__ = "conversation_states"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(64), default="MENU")
    context: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="conversation")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(500), default="")
    salary: Mapped[str] = mapped_column(String(120), default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    applications: Mapped[list[Application]] = relationship(back_populates="job")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_application_user_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), default=ApplicationStatus.applied
    )
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship(back_populates="applications")


class SupportTicket(Base):
    """A message a user sent via Contact us, and the team's reply."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    message: Mapped[str] = mapped_column(Text)
    reply: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()


class ProcessedMessage(Base):
    """Inbound WhatsApp message ids we have already handled. Meta redelivers on timeouts."""

    __tablename__ = "processed_messages"

    wa_message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class OutboundMessage(Base):
    """Every message we send, with the delivery status WhatsApp reports back."""

    __tablename__ = "outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    wa_message_id: Mapped[str | None] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    delivery_status: Mapped[str] = mapped_column(String(32), default="sent")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AlertSent(Base):
    """Guarantees a user is never alerted twice about the same job."""

    __tablename__ = "alerts_sent"
    __table_args__ = (UniqueConstraint("user_id", "job_id", name="uq_alert_user_job"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
