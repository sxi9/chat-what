from datetime import timedelta

from jobbot import db as dbmod
from jobbot.models import Application, User, UserStatus, utcnow

H = {"X-Admin-Key": "test-admin-key"}


def test_admin_requires_key(client):
    assert client.get("/admin/jobs").status_code == 401
    assert client.get("/admin/jobs", headers={"X-Admin-Key": "nope"}).status_code == 401
    assert client.get("/admin/jobs", headers=H).status_code == 200


def test_create_list_and_patch_job(client):
    r = client.post("/admin/jobs", headers=H, json={"title": "Cook", "company": "Cafe", "location": "Nakuru"})
    assert r.status_code == 201
    jid = r.json()["id"]
    assert client.get("/admin/jobs", headers=H).json()[0]["title"] == "Cook"
    r = client.patch(f"/admin/jobs/{jid}", headers=H, json={"is_active": False})
    assert r.json()["is_active"] is False
    assert client.get("/admin/jobs", headers=H, params={"active_only": True}).json() == []
    assert client.patch("/admin/jobs/999", headers=H, json={"title": "x"}).status_code == 404


def test_application_status_change_notifies_user(client, sender, seed_jobs, settings):
    with dbmod.session_scope() as db:
        u = User(phone="300", name="Asha", status=UserStatus.active, notifications_opt_in=True,
                 last_inbound_at=utcnow() - timedelta(days=3))
        db.add(u); db.flush()
        app = Application(user_id=u.id, job_id=seed_jobs[0]); db.add(app); db.flush()
        app_id = app.id
    r = client.patch(f"/admin/applications/{app_id}", headers=H,
                     json={"status": "interview", "note": "Tuesday 10am"})
    assert r.status_code == 200 and r.json()["status"] == "interview"
    assert len(sender.sent) == 1 and sender.sent[0]["type"] == "template"
    assert sender.sent[0]["template"]["name"] == settings.wa_template_status_update
    params = [p["text"] for p in sender.sent[0]["template"]["components"][0]["parameters"]]
    assert params[-1] == "Interview stage"
    # same status again: no change, no message
    client.patch(f"/admin/applications/{app_id}", headers=H, json={"status": "interview"})
    assert len(sender.sent) == 1
    # inside the service window the notification is a free-form button message
    with dbmod.session_scope() as db:
        db.query(User).filter_by(phone="300").one().last_inbound_at = utcnow()
    client.patch(f"/admin/applications/{app_id}", headers=H, json={"status": "offer"})
    assert sender.sent[-1]["type"] == "interactive" and "Offer made" in sender.sent[-1]["interactive"]["body"]["text"]


def test_application_status_change_respects_opt_out_and_notify_flag(client, sender, seed_jobs):
    with dbmod.session_scope() as db:
        u = User(phone="301", status=UserStatus.active, notifications_opt_in=False)
        db.add(u); db.flush()
        app = Application(user_id=u.id, job_id=seed_jobs[0]); db.add(app); db.flush()
        app_id = app.id
    client.patch(f"/admin/applications/{app_id}", headers=H, json={"status": "rejected"})
    assert sender.sent == []
    with dbmod.session_scope() as db:
        db.query(User).filter_by(phone="301").one().notifications_opt_in = True
    client.patch(f"/admin/applications/{app_id}", headers=H, json={"status": "offer", "notify": False})
    assert sender.sent == []


def test_ticket_reply_reaches_user(client, sender, onboarded, settings):
    onboarded.pick("menu:contact")
    onboarded.say("Please delete my data")
    sender.clear()
    tickets = client.get("/admin/tickets", headers=H, params={"status": "open"}).json()
    assert len(tickets) == 1 and "delete" in tickets[0]["message"]
    r = client.post(f"/admin/tickets/{tickets[0]['id']}/reply", headers=H,
                    json={"reply": "Done, your data has been removed.", "close": True})
    assert r.json()["status"] == "closed"
    assert sender.sent[-1]["type"] == "interactive"  # user wrote minutes ago: inside window
    assert "Done, your data" in sender.sent[-1]["interactive"]["body"]["text"]
    assert client.get("/admin/tickets", headers=H, params={"status": "open"}).json() == []


def test_alerts_run_endpoint(client, sender, onboarded):
    r = client.post("/admin/alerts/run", headers=H)
    assert r.status_code == 200 and r.json()["users_alerted"] == 1
