"""Transcript and long-term memory helpers for the assistant."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatMessage, Memory, User, utcnow


def add_message(db: Session, user: User, role: str, content: str) -> None:
    db.add(ChatMessage(user_id=user.id, role=role, content=content[:8000]))
    db.flush()


def recent_messages(db: Session, user: User, turns: int) -> list[dict]:
    """Last `turns` messages as API-shaped dicts, oldest first, same-role runs merged."""
    rows = db.scalars(
        select(ChatMessage).where(ChatMessage.user_id == user.id)
        .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(turns)
    ).all()
    out: list[dict] = []
    for r in reversed(rows):
        if out and out[-1]["role"] == r.role:
            out[-1]["content"] += "\n" + r.content
        else:
            out.append({"role": r.role, "content": r.content})
    if out and out[0]["role"] == "assistant":
        out.pop(0)  # history must start with a user turn
    return out


def remember(db: Session, user: User, key: str, value: str) -> None:
    key = key.strip().lower()[:64]
    row = db.scalar(select(Memory).where(Memory.user_id == user.id, Memory.key == key))
    if row:
        row.value = value[:2000]
        row.updated_at = utcnow()
    else:
        db.add(Memory(user_id=user.id, key=key, value=value[:2000]))
    db.flush()


def forget(db: Session, user: User, key: str) -> bool:
    row = db.scalar(select(Memory).where(Memory.user_id == user.id, Memory.key == key.strip().lower()))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def memories(db: Session, user: User) -> list[Memory]:
    return db.scalars(select(Memory).where(Memory.user_id == user.id).order_by(Memory.updated_at.desc())).all()
