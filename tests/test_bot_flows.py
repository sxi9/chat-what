from jobbot import db as dbmod
from jobbot.models import Application, ApplicationStatus, SupportTicket, User


def user(phone="15550001111") -> User:
    with dbmod.session_scope() as db:
        u = db.query(User).filter_by(phone=phone).one()
        db.refresh(u)
        _ = u.conversation.state
        return u


def test_onboarding_asks_name_then_consent_then_shows_menu(chat):
    (welcome,) = chat.say("hello")
    assert "what's your name" in welcome.body
    (consent,) = chat.say("Asha")
    assert consent.button_ids == ["notify:on", "notify:off"]
    (menu,) = chat.tap("notify:on")
    assert menu.interactive_type == "list"
    assert set(menu.row_ids) == {"menu:jobs", "menu:applications", "menu:faq", "menu:contact", "menu:settings"}
    u = user()
    assert u.name == "Asha" and u.status.value == "active" and u.notifications_opt_in is True


def test_consent_accepts_typed_yes_no(chat):
    chat.say("hi")
    chat.say("Asha")
    (menu,) = chat.say("no")
    assert "Notifications are off" in menu.body
    assert user().notifications_opt_in is False


def test_stale_button_during_onboarding_repeats_question(chat):
    chat.say("hi")
    (again,) = chat.tap("menu:jobs")
    assert "what's your name" in again.body


def test_unknown_text_returns_menu_without_guessing(onboarded):
    (reply,) = onboarded.say("asdfgh qwerty")
    assert "didn't catch that" in reply.body and reply.interactive_type == "list"


def test_unsupported_media_returns_menu(onboarded):
    onboarded._send("other", "")
    assert "only read text and button taps" in onboarded.last().body


def test_jobs_entry_asks_preferences_first_time(chat, seed_jobs):
    chat.say("hi"); chat.say("Asha"); chat.tap("notify:on")
    (ask,) = chat.pick("menu:jobs")
    assert "What kind of work" in ask.body
    (ask_loc,) = chat.say("accountant")
    assert "Which city" in ask_loc.body
    saved, jobs = chat.say("Nairobi")
    assert "Preferences saved" in saved.body
    assert jobs.row_ids == [f"job:{seed_jobs[1]}"]  # only the accountant job matches


def test_job_list_ranks_matches_and_hides_inactive(onboarded, seed_jobs):
    (jobs,) = onboarded.pick("menu:jobs")
    ids = jobs.row_ids
    assert ids[0] == f"job:{seed_jobs[0]}"  # Nairobi delivery driver first
    assert f"job:{seed_jobs[2]}" in ids       # Mombasa taxi driver still shown, lower
    assert f"job:{seed_jobs[4]}" not in ids   # inactive job hidden
    assert f"job:{seed_jobs[1]}" not in ids   # accountant does not match "driver, delivery"


def test_job_detail_apply_and_status(onboarded, seed_jobs):
    jid = seed_jobs[0]
    (detail,) = onboarded.pick(f"job:{jid}")
    assert "Delivery Driver" in detail.body and detail.button_ids[0] == f"apply:{jid}"
    (done,) = onboarded.tap(f"apply:{jid}")
    assert "You've applied" in done.body
    with dbmod.session_scope() as db:
        app = db.query(Application).one()
        assert app.job_id == jid and app.status == ApplicationStatus.applied
        app_id = app.id
    (detail_again,) = onboarded.pick(f"job:{jid}")
    assert detail_again.button_ids[0] == f"app:{app_id}"  # cannot apply twice
    (status,) = onboarded.pick("menu:applications")
    assert status.row_ids == [f"app:{app_id}"]
    (app_detail,) = onboarded.pick(f"app:{app_id}")
    assert "Status: *Applied*" in app_detail.body and f"withdraw:{app_id}" in app_detail.button_ids


def test_apply_twice_is_idempotent(onboarded, seed_jobs):
    jid = seed_jobs[0]
    onboarded.tap(f"apply:{jid}")
    (reply,) = onboarded.tap(f"apply:{jid}")
    assert "already applied" in reply.body
    with dbmod.session_scope() as db:
        assert db.query(Application).count() == 1


def test_withdraw_requires_confirmation(onboarded, seed_jobs):
    onboarded.tap(f"apply:{seed_jobs[0]}")
    with dbmod.session_scope() as db:
        app_id = db.query(Application).one().id
    (confirm,) = onboarded.tap(f"withdraw:{app_id}")
    assert confirm.button_ids == [f"confirm_withdraw:{app_id}", f"app:{app_id}"]
    (kept,) = onboarded.tap(f"app:{app_id}")
    assert "Status: *Applied*" in kept.body
    note, status = onboarded.tap(f"confirm_withdraw:{app_id}")
    assert "withdrawn" in note.body
    with dbmod.session_scope() as db:
        assert db.query(Application).one().status == ApplicationStatus.withdrawn
    (detail,) = onboarded.pick(f"app:{app_id}")
    assert f"withdraw:{app_id}" not in detail.button_ids


def test_missing_job_falls_back_to_list(onboarded):
    note, jobs = onboarded.pick("job:9999")
    assert "no longer available" in note.body and jobs.interactive_type == "list"


def test_text_shortcuts(onboarded):
    assert onboarded.say("jobs")[-1].interactive_type == "list"
    assert "haven't applied" in onboarded.say("status")[-1].body
    assert "Notifications" in onboarded.say("settings")[-1].body
    assert onboarded.say("menu")[-1].interactive_type == "list"


def test_stop_pauses_notifications_and_can_resume(onboarded):
    (reply,) = onboarded.say("STOP")
    assert "paused" in reply.body and "notify:on" in reply.button_ids
    assert user().notifications_opt_in is False
    (menu,) = onboarded.tap("notify:on")
    assert "Notifications are on" in menu.body
    assert user().notifications_opt_in is True


def test_settings_toggle_and_edit_preferences(onboarded):
    (settings,) = onboarded.pick("menu:settings")
    assert "settings:notify_off" in settings.button_ids
    (settings,) = onboarded.tap("settings:notify_off")
    assert "Notifications: *Off*" in settings.body
    (ask,) = onboarded.tap("prefs:edit")
    assert "What kind of work" in ask.body
    onboarded.say("python, backend")
    (menu,) = onboarded.say("any")
    assert "Preferences saved" in menu.body and menu.interactive_type == "list"
    u = user()
    assert u.keywords == "python, backend" and u.location == "any"
    (ask_name,) = onboarded.tap("settings:name")
    (settings,) = onboarded.say("Asha K")
    assert "Name updated" in settings.body and user().name == "Asha K"


def test_faq_list_and_answer(onboarded):
    (faq,) = onboarded.pick("menu:faq")
    assert faq.interactive_type == "list" and "faq:cost" in faq.row_ids
    (answer,) = onboarded.pick("faq:cost")
    assert "Job seekers never pay" in answer.body and "menu:faq" in answer.button_ids
    (relist,) = onboarded.pick("faq:nonexistent")
    assert "don't have that topic" in relist.body and relist.interactive_type == "list"


def test_contact_creates_ticket(onboarded):
    (ask,) = onboarded.pick("menu:contact")
    assert "Type your message" in ask.body
    (short,) = onboarded.say("ok")
    assert "more detail" in short.body
    (received,) = onboarded.say("My location is wrong, please change it to Kisumu")
    assert "Ticket T1" in received.body
    with dbmod.session_scope() as db:
        t = db.query(SupportTicket).one()
        assert "Kisumu" in t.message and t.status.value == "open"


def test_menu_word_exits_a_question(onboarded):
    onboarded.pick("menu:contact")
    (menu,) = onboarded.say("menu")
    assert menu.interactive_type == "list"
    with dbmod.session_scope() as db:
        assert db.query(SupportTicket).count() == 0


def test_pagination_offers_more_when_matches_exceed_page(onboarded, seed_jobs):
    # recommendations_limit is 3 in tests; add enough driver jobs to overflow one page.
    from jobbot.models import Job
    with dbmod.session_scope() as db:
        for i in range(5):
            db.add(Job(title=f"Driver {i}", company="Fleet", location="Nairobi", tags="driver"))
    (page1,) = onboarded.pick("menu:jobs")
    assert page1.row_ids[-1] == "jobs:more" and len(page1.row_ids) == 4
    (page2,) = onboarded.pick("jobs:more")
    assert set(page1.row_ids[:-1]).isdisjoint(set(page2.row_ids))


def test_two_users_have_independent_state(bot, sender, seed_jobs):
    from tests.conftest import Chat
    a, b = Chat(bot, sender, "15550000001"), Chat(bot, sender, "15550000002")
    a.say("hi"); a.say("Alpha"); a.tap("notify:on")
    b.say("hi")
    assert "what's your name" in b.last().body
    a.pick("menu:contact")
    b.say("Beta")
    assert b.last().button_ids == ["notify:on", "notify:off"]
    assert user("15550000001").conversation.state == "CONTACT_ASK_MESSAGE"
