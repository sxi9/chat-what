"""Deterministic job matching. No model, just a transparent score."""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, ApplicationStatus, Job, User, utcnow

_WORD = re.compile(r"[a-z0-9+#.]+")

ANY_LOCATION = {"", "any", "anywhere", "all", "remote"}


def tokens(text: str | None) -> set[str]:
    return set(_WORD.findall((text or "").lower()))


def location_matches(user_location: str, job_location: str) -> bool:
    u = (user_location or "").strip().lower()
    j = (job_location or "").strip().lower()
    if u in ANY_LOCATION:
        return True
    if "remote" in j:
        return True
    return u in j or j in u


def score_job(user: User, job: Job, now=None) -> int:
    """Higher is better. 0 means no match at all."""
    now = now or utcnow()
    kws = user.keyword_list()
    title_t = tokens(job.title)
    body_t = tokens(job.description) | tokens(job.tags) | tokens(job.company)
    score = 0
    for kw in kws:
        kw_t = tokens(kw)
        if not kw_t:
            continue
        if kw_t <= title_t:
            score += 5
        elif kw_t & title_t:
            score += 3
        if kw_t <= body_t:
            score += 2
        elif kw_t & body_t:
            score += 1
    if score == 0:
        return 0
    if location_matches(user.location, job.location):
        score += 2
    elif (user.location or "").strip().lower() not in ANY_LOCATION:
        score -= 2  # wrong city, still visible but ranked low
    age_days = max(0, (now - job.posted_at).days)
    if age_days <= 3:
        score += 2
    elif age_days <= 7:
        score += 1
    return max(score, 0)


def applied_job_ids(db: Session, user: User) -> set[int]:
    rows = db.execute(select(Application.job_id).where(Application.user_id == user.id)).all()
    return {r[0] for r in rows}


def recommend(db: Session, user: User, limit: int = 5, exclude: set[int] | None = None,
              max_age_days: int | None = None) -> list[Job]:
    """Top matching active jobs the user has not applied to, best first."""
    q = select(Job).where(Job.is_active.is_(True))
    if max_age_days is not None:
        q = q.where(Job.posted_at >= utcnow() - timedelta(days=max_age_days))
    jobs = db.scalars(q).all()
    skip = applied_job_ids(db, user) | (exclude or set())
    scored = [(score_job(user, j), j) for j in jobs if j.id not in skip]
    scored = [(s, j) for s, j in scored if s > 0]
    scored.sort(key=lambda sj: (-sj[0], -sj[1].posted_at.timestamp(), sj[1].id))
    return [j for _, j in scored[:limit]]


def latest_jobs(db: Session, limit: int = 5) -> list[Job]:
    """Fallback when a user's keywords match nothing."""
    return db.scalars(
        select(Job).where(Job.is_active.is_(True)).order_by(Job.posted_at.desc(), Job.id.desc()).limit(limit)
    ).all()


def get_job(db: Session, job_id: int) -> Job | None:
    return db.get(Job, job_id)


def format_job_detail(job: Job) -> str:
    lines = [f"*{job.title}*", f"{job.company} · {job.location or 'Location not stated'}"]
    if job.salary:
        lines.append(f"Salary: {job.salary}")
    lines.append("")
    desc = job.description.strip() or "No description provided."
    if len(desc) > 600:
        desc = desc[:597].rstrip() + "..."
    lines.append(desc)
    if job.url:
        lines += ["", job.url]
    lines += ["", f"Posted {job.posted_at.strftime('%d %b %Y')} · Ref J{job.id}"]
    return "\n".join(lines)
