"""Jobs module: preferences, recommendations, applying, application status, withdrawal."""
from __future__ import annotations

from ...models import ApplicationStatus
from ...services import applications as apps
from ...services import jobs as jobsvc
from ...whatsapp import messages as m
from ..context import Ctx
from . import MenuEntry, Module

ASK_KEYWORDS = "JOBS_ASK_KEYWORDS"
ASK_LOCATION = "JOBS_ASK_LOCATION"
LIST = "JOBS_LIST"
DETAIL = "JOBS_DETAIL"
STATUS = "JOBS_STATUS"
APP_DETAIL = "JOBS_APP_DETAIL"

ASK_KEYWORDS_TXT = ("What kind of work are you looking for, {name}?\n\n"
                    "Send a few words separated by commas, for example *driver, delivery* or *accountant, finance*.")
ASK_LOCATION_TXT = "Which city or area? Send *any* if you're open to working anywhere."
PREFS_SAVED = "Preferences saved."
LIST_BODY = "Your top matches for *{keywords}* in *{location}*. Tap one for details."
FALLBACK_BODY = ("Nothing matched *{keywords}* yet, so here are the newest jobs. "
                 "Update your keywords in Settings to improve matches.")
EMPTY = "No jobs are posted yet. I'll message you as soon as matching jobs arrive."
MISSING = "That job is no longer available."
APPLIED = ("Done. You've applied for *{title}* at {company}.\n\nRef A{ref}. "
           "I'll message you as soon as the employer updates your status.")
ALREADY_APPLIED = "You've already applied for *{title}*. Here is where it stands."
STATUS_BODY = "Your applications, most recent first. Tap one for details."
STATUS_EMPTY = "You haven't applied for anything yet."
APP_MISSING = "I couldn't find that application."
WITHDRAW_CONFIRM = "Withdraw your application for *{title}* at {company}? This can't be undone."
WITHDRAWN = "Your application for *{title}* has been withdrawn."
WITHDRAW_IMPOSSIBLE = "That application can't be withdrawn any more."

BTN_FIND = "Find jobs"
BTN_APPS = "My applications"
BTN_MENU = "Main menu"
BTN_APPLY = "Apply"
BTN_MORE = "More jobs"
BTN_VIEW_APP = "View application"
BTN_WITHDRAW = "Withdraw"
BTN_YES_WITHDRAW = "Yes, withdraw"
BTN_KEEP = "Keep it"
BTN_SETTINGS = "Settings"


class JobsModule(Module):
    key = "jobs"
    entries = (
        MenuEntry("jobs", BTN_FIND, "Jobs matching your skills and area"),
        MenuEntry("applications", BTN_APPS, "Status of jobs you applied for"),
    )
    commands = ("job", "jobs", "apply", "app", "withdraw", "confirm_withdraw", "prefs")
    keywords = {"jobs": "jobs", "job": "jobs", "find jobs": "jobs", "vacancies": "jobs",
                "status": "applications", "applications": "applications",
                "my applications": "applications", "application status": "applications"}
    states = (ASK_KEYWORDS, ASK_LOCATION)  # only states that asked for free text

    # ----- Module contract ---------------------------------------------------

    def open(self, ctx: Ctx, entry: str) -> None:
        if entry == "applications":
            self.show_status(ctx)
        elif not ctx.user.keyword_list():
            self.start_prefs(ctx, then="jobs")
        else:
            self.show_jobs(ctx)

    def command(self, ctx: Ctx, action: str, arg: str) -> None:
        if action == "jobs" and arg == "more":
            self.show_jobs(ctx, page=ctx.data.get("page", 0) + 1)
        elif action == "prefs" and arg == "edit":
            self.start_prefs(ctx, then="settings")
        elif arg.isdigit():
            n = int(arg)
            {"job": self.show_job, "apply": self.apply, "app": self.show_application,
             "withdraw": self.confirm_withdraw, "confirm_withdraw": self.withdraw}.get(
                action, lambda c, _: c.menu())(ctx, n)
        else:
            ctx.menu()

    def text(self, ctx: Ctx, text: str) -> None:
        answer = text.strip()
        if ctx.state == ASK_KEYWORDS and answer:
            ctx.user.keywords = ", ".join(k.strip() for k in answer.replace("\n", ",").split(",") if k.strip())[:500]
            ctx.set_state(ASK_LOCATION, ctx.data)
            ctx.text(ASK_LOCATION_TXT, kind="ask_location")
        elif ctx.state == ASK_LOCATION and answer:
            ctx.user.location = answer[:120]
            then = ctx.data.get("then", "jobs")
            if then == "settings":
                ctx.set_state("MENU")
                ctx.menu(PREFS_SAVED)
            else:
                ctx.text(PREFS_SAVED, kind="note")
                self.show_jobs(ctx)
        elif ctx.state in (ASK_KEYWORDS, ASK_LOCATION):
            self._ask_current(ctx)
        else:
            ctx.menu()

    # ----- preferences -------------------------------------------------------

    def start_prefs(self, ctx: Ctx, then: str) -> None:
        ctx.set_state(ASK_KEYWORDS, {"then": then})
        ctx.text(ASK_KEYWORDS_TXT.format(name=ctx.user.name or "there"), kind="ask_keywords")

    def _ask_current(self, ctx: Ctx) -> None:
        if ctx.state == ASK_KEYWORDS:
            ctx.text(ASK_KEYWORDS_TXT.format(name=ctx.user.name or "there"), kind="ask_keywords")
        else:
            ctx.text(ASK_LOCATION_TXT, kind="ask_location")

    # ----- jobs --------------------------------------------------------------

    def show_jobs(self, ctx: Ctx, page: int = 0) -> None:
        per_page = min(ctx.settings.recommendations_limit, m.LIST_ROWS_MAX - 1)
        need = per_page * (page + 1) + 1
        matched = jobsvc.recommend(ctx.db, ctx.user, limit=need)
        fallback = False
        if not matched:
            matched = jobsvc.latest_jobs(ctx.db, limit=need)
            fallback = True
        if not matched:
            ctx.set_state(LIST)
            ctx.buttons(EMPTY, [m.Button("menu:settings", BTN_SETTINGS), m.Button("menu:main", BTN_MENU)],
                        kind="jobs_empty")
            return
        start = page * per_page
        page_jobs = matched[start:start + per_page]
        if not page_jobs:
            page, page_jobs = 0, matched[:per_page]
        has_more = len(matched) > (page + 1) * per_page
        ctx.set_state(LIST, {"page": page})
        rows = [m.Row(f"job:{j.id}", j.title, f"{j.company} · {j.location or 'Location not stated'}")
                for j in page_jobs]
        if has_more:
            rows.append(m.Row("jobs:more", BTN_MORE, "Show the next matches"))
        body = (FALLBACK_BODY if fallback else LIST_BODY).format(
            keywords=ctx.user.keywords or "anything", location=ctx.user.location or "any location")
        ctx.list(body, "View jobs", rows, section_title="Newest jobs" if fallback else "Matches",
                 footer="Tap a job to see details and apply", kind="jobs_list")

    def show_job(self, ctx: Ctx, job_id: int) -> None:
        job = jobsvc.get_job(ctx.db, job_id)
        if job is None or not job.is_active:
            ctx.text(MISSING, kind="note")
            self.show_jobs(ctx)
            return
        ctx.set_state(DETAIL, {"job_id": job.id})
        existing = next((a for a in ctx.user.applications if a.job_id == job.id), None)
        first = m.Button(f"app:{existing.id}", BTN_VIEW_APP) if existing else m.Button(f"apply:{job.id}", BTN_APPLY)
        ctx.buttons(jobsvc.format_job_detail(job),
                    [first, m.Button("menu:jobs", BTN_MORE), m.Button("menu:main", BTN_MENU)], kind="job_detail")

    def apply(self, ctx: Ctx, job_id: int) -> None:
        job = jobsvc.get_job(ctx.db, job_id)
        if job is None or not job.is_active:
            ctx.text(MISSING, kind="note")
            self.show_jobs(ctx)
            return
        app, created = apps.apply(ctx.db, ctx.user, job)
        if not created:
            ctx.set_state(APP_DETAIL, {"app_id": app.id})
            ctx.buttons(ALREADY_APPLIED.format(title=job.title) + "\n\n" + apps.format_application(app),
                        [m.Button("menu:applications", BTN_APPS), m.Button("menu:main", BTN_MENU)],
                        kind="already_applied")
            return
        ctx.set_state("MENU")
        ctx.buttons(APPLIED.format(title=job.title, company=job.company, ref=app.id),
                    [m.Button("menu:applications", BTN_APPS), m.Button("menu:jobs", BTN_FIND),
                     m.Button("menu:main", BTN_MENU)], kind="applied")

    # ----- applications ------------------------------------------------------

    def show_status(self, ctx: Ctx) -> None:
        items = apps.list_for_user(ctx.db, ctx.user, limit=m.LIST_ROWS_MAX)
        ctx.set_state(STATUS)
        if not items:
            ctx.buttons(STATUS_EMPTY, [m.Button("menu:jobs", BTN_FIND), m.Button("menu:main", BTN_MENU)],
                        kind="status_empty")
            return
        rows = [m.Row(f"app:{a.id}", a.job.title, f"{a.job.company} · {apps.status_label(a.status)}") for a in items]
        ctx.list(STATUS_BODY, "View applications", rows, section_title="Applications", kind="status_list")

    def show_application(self, ctx: Ctx, app_id: int) -> None:
        app = apps.get_for_user(ctx.db, ctx.user, app_id)
        if app is None:
            ctx.text(APP_MISSING, kind="note")
            self.show_status(ctx)
            return
        ctx.set_state(APP_DETAIL, {"app_id": app.id})
        buttons = []
        if app.status not in (ApplicationStatus.withdrawn, ApplicationStatus.rejected):
            buttons.append(m.Button(f"withdraw:{app.id}", BTN_WITHDRAW))
        buttons += [m.Button("menu:applications", BTN_APPS), m.Button("menu:main", BTN_MENU)]
        ctx.buttons(apps.format_application(app), buttons, kind="app_detail")

    def confirm_withdraw(self, ctx: Ctx, app_id: int) -> None:
        app = apps.get_for_user(ctx.db, ctx.user, app_id)
        if app is None:
            self.show_status(ctx)
            return
        ctx.buttons(WITHDRAW_CONFIRM.format(title=app.job.title, company=app.job.company),
                    [m.Button(f"confirm_withdraw:{app.id}", BTN_YES_WITHDRAW), m.Button(f"app:{app.id}", BTN_KEEP)],
                    kind="withdraw_confirm")

    def withdraw(self, ctx: Ctx, app_id: int) -> None:
        app = apps.get_for_user(ctx.db, ctx.user, app_id)
        if app is None:
            self.show_status(ctx)
            return
        ctx.text(WITHDRAWN.format(title=app.job.title) if apps.withdraw(ctx.db, app) else WITHDRAW_IMPOSSIBLE,
                 kind="note")
        self.show_status(ctx)
