from datetime import timedelta

from jobbot import db as dbmod
from jobbot.models import Job, User, utcnow
from jobbot.services import jobs as jobsvc


def make_user(keywords, location):
    return User(phone="1", keywords=keywords, location=location)


def test_score_prefers_title_match_then_location_then_recency():
    now = utcnow()
    u = make_user("driver", "Nairobi")
    title_local = Job(title="Delivery Driver", company="A", location="Nairobi", posted_at=now)
    title_remote_city = Job(title="Delivery Driver", company="A", location="Mombasa", posted_at=now)
    body_only = Job(title="Courier", company="A", location="Nairobi", description="driver needed", posted_at=now)
    old = Job(title="Delivery Driver", company="A", location="Nairobi", posted_at=now - timedelta(days=20))
    nomatch = Job(title="Accountant", company="A", location="Nairobi", posted_at=now)
    s = {k: jobsvc.score_job(u, j, now) for k, j in locals().items() if isinstance(j, Job)}
    assert s["title_local"] > s["title_remote_city"]
    assert s["title_local"] > s["body_only"] > 0
    assert s["title_local"] > s["old"] > 0
    assert s["nomatch"] == 0


def test_any_location_and_remote_jobs_match_everyone():
    assert jobsvc.location_matches("any", "Nairobi")
    assert jobsvc.location_matches("Kisumu", "Remote (Kenya)")
    assert jobsvc.location_matches("nairobi", "Nairobi CBD")
    assert not jobsvc.location_matches("Kisumu", "Nairobi")


def test_recommend_excludes_applied_and_explicit_ids(seed_jobs):
    with dbmod.session_scope() as db:
        u = User(phone="2", keywords="driver", location="any")
        db.add(u)
        db.flush()
        ids = [j.id for j in jobsvc.recommend(db, u, limit=10)]
        assert seed_jobs[0] in ids and seed_jobs[2] in ids and seed_jobs[4] not in ids
        ids2 = [j.id for j in jobsvc.recommend(db, u, limit=10, exclude={seed_jobs[0]})]
        assert seed_jobs[0] not in ids2
        from jobbot.services import applications as apps
        apps.apply(db, u, db.get(Job, seed_jobs[2]))
        ids3 = [j.id for j in jobsvc.recommend(db, u, limit=10, exclude={seed_jobs[0]})]
        assert ids3 == []


def test_recommend_respects_max_age(seed_jobs):
    with dbmod.session_scope() as db:
        u = User(phone="3", keywords="support", location="any")
        db.add(u)
        db.flush()
        assert len(jobsvc.recommend(db, u, limit=10)) == 1
        assert jobsvc.recommend(db, u, limit=10, max_age_days=2) == []
