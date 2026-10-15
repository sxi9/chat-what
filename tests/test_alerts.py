from datetime import timedelta

from jobbot import db as dbmod
from jobbot.api.deps import get_notifier
from jobbot.models import AlertSent, Job, User, UserStatus, utcnow
from jobbot.services.alerts import run_alerts


def make_user(db, phone, keywords="driver", location="Nairobi", opt_in=True, in_window=False):
    u = User(phone=phone, name="Asha", keywords=keywords, location=location, status=UserStatus.active,
             notifications_opt_in=opt_in,
             last_inbound_at=utcnow() - timedelta(hours=1 if in_window else 48))
    db.add(u)
    db.flush()
    return u


def test_alert_uses_list_inside_window_and_templates_outside(sender, settings, seed_jobs):
    with dbmod.session_scope() as db:
        make_user(db, "100", in_window=True)
        make_user(db, "200", in_window=False)
        report = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
    assert report.users_alerted == 2 and report.jobs_sent == 4 and report.failures == 0
    by_to = {}
    for p in sender.sent:
        by_to.setdefault(p["to"], []).append(p)
    assert [p["type"] for p in by_to["100"]] == ["interactive"]
    assert by_to["100"][0]["interactive"]["type"] == "list"
    assert [p["type"] for p in by_to["200"]] == ["template", "template"]
    assert by_to["200"][0]["template"]["name"] == settings.wa_template_job_alert


def test_alert_never_repeats_a_job(sender, seed_jobs):
    with dbmod.session_scope() as db:
        make_user(db, "100")
        first = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
        second = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
        assert first.jobs_sent == 2 and second.jobs_sent == 0
        assert db.query(AlertSent).count() == 2
        db.add(Job(title="Night Driver", company="New", location="Nairobi", tags="driver"))
        db.flush()
        third = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
        assert third.jobs_sent == 1


def test_alerts_skip_opted_out_and_users_without_keywords(sender, seed_jobs):
    with dbmod.session_scope() as db:
        make_user(db, "100", opt_in=False)
        make_user(db, "200", keywords="")
        report = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
    assert report.users_alerted == 0 and sender.sent == []


def test_failed_send_is_reported_and_not_marked_sent(sender, seed_jobs):
    sender.fail = True
    with dbmod.session_scope() as db:
        make_user(db, "100")
        report = run_alerts(db, get_notifier(), per_user=3, lookback_days=14)
        assert report.failures == 1 and db.query(AlertSent).count() == 0
