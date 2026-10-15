# chat-what

A deterministic WhatsApp chatbot with its own backend. WhatsApp is the whole user interface:
no website, no app, no language model. Every reply is a menu, a list or a button, driven by a
finite state machine, so the bot never guesses and every path is testable.

It ships with four modules and a framework for adding more:

| Module | What the user can do |
| --- | --- |
| Jobs | Set job keywords and location, browse ranked matches, apply with one tap, see each application's stage, withdraw |
| Help & FAQ | Pick a question from a list, get the answer. Content lives in a JSON file |
| Contact us | Send a message to the team, which becomes a ticket. The team's reply is delivered back on WhatsApp |
| Settings | Notifications on/off, job preferences, name |

Outbound, without the user asking: job alerts for new matches, application status changes, and
ticket replies. All of these respect WhatsApp's 24-hour rule (see below).

## How it works

```
WhatsApp user ──► Meta Cloud API ──► POST /webhook ──► Bot (state machine) ──► modules ──► SQLite
                                                                                 │
recruiter / ops ──► /admin/* (API key) ──► jobs, applications, tickets ──► Notifier ──► Meta Cloud API ──► user
cron ──► jobbot send-alerts ──► matches since last alert ──► Notifier
```

* `jobbot/whatsapp/` talks to Meta. `messages.py` builds payloads and enforces Meta's length
  limits. `webhook.py` verifies the `X-Hub-Signature-256` signature and normalises inbound
  JSON into one `Inbound` dataclass. `client.py` sends; `Sender` is a protocol, so Twilio or a
  test double can replace the Meta client.
* `jobbot/bot/engine.py` is the core: onboarding (name, notification consent), the main menu,
  global words (`menu`, `stop`, `jobs`, `status`, ...), duplicate-message protection and routing.
  Button and list ids are commands shaped `action:arg`; the prefix selects the module, so a user
  can tap a week-old message and still get a sensible answer.
* `jobbot/bot/modules/` holds the features. A module declares its menu entries, the command
  prefixes it handles, the text words that open it, and the states in which it asked a question.
* `jobbot/services/` is the backend logic: deterministic job scoring, applications, alerts, and
  the `Notifier`, which picks a free-form message inside the 24-hour window and an approved
  template outside it.
* `jobbot/api/admin.py` is how data gets in: post jobs, move applications through stages, answer
  tickets, trigger an alert run. Protected by the `X-Admin-Key` header.

### The 24-hour rule

WhatsApp lets a business send anything for 24 hours after the user's last message. Outside that
window only pre-approved **message templates** are accepted. The bot tracks `last_inbound_at`
per user and `Notifier` switches automatically. You must create these templates in WhatsApp
Manager and get them approved before alerts work for quiet users:

| Setting | Body variables, in order |
| --- | --- |
| `WA_TEMPLATE_JOB_ALERT` | name, job title, company, location, reference |
| `WA_TEMPLATE_STATUS_UPDATE` | name, job title, company, status label |
| `WA_TEMPLATE_TICKET_REPLY` | name, ticket reference, reply text |

Example body for `job_alert`: `Hi {{1}}, a new job matches your search: {{2}} at {{3}} ({{4}}).
Reply JOBS to see it. Ref {{5}}.` Classify them as *utility*, keep them specific to what the user
asked for, and they will be approved faster and cost less than marketing templates.

Users must opt in before receiving notifications. The bot asks during onboarding, stores the
timestamp, and honours STOP from any state.

## Run it locally

```bash
pip install -e ".[dev]"
cp .env.example .env            # fill in later; the simulator needs nothing
jobbot seed                     # creates the SQLite db and inserts demo jobs
jobbot chat                     # talk to the bot in your terminal
```

The simulator prints exactly what WhatsApp would show, including button and row ids. Type text
to send a text message, or an id such as `menu:jobs`, `job:1` or `apply:1` to tap. This is how
you develop and demo without a Meta account.

```bash
pytest -q                       # 45 tests: message limits, webhook, every conversation path, scoring, alerts, admin API
uvicorn jobbot.main:app --reload
```

## Connect it to WhatsApp

1. In [Meta for Developers](https://developers.facebook.com/) create an app, add the WhatsApp
   product, and note the **Phone number ID** and a **token** (use a permanent System User token in
   production). Meta gives you a free test number that can message up to five verified phones.
2. Put the values in `.env` (`WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_APP_SECRET`, and your
   own `WA_VERIFY_TOKEN`).
3. Run the API on a public HTTPS URL (for development, `ngrok http 8000`).
4. In the app's WhatsApp > Configuration page set the webhook URL to `https://your-host/webhook`
   with the same verify token, and subscribe to the `messages` field.
5. Send "hi" to the number.

For production: verify your business, buy or port a number, create the three templates above,
and move `DATABASE_URL` to Postgres (SQLAlchemy, nothing else changes).

## Operate it

Everything the users see is written through the admin API. Set `ADMIN_API_KEY` and send it as
`X-Admin-Key`.

```bash
# post a job
curl -X POST localhost:8000/admin/jobs -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title":"Delivery Driver","company":"QuickShip","location":"Nairobi","tags":"driver, delivery","description":"..."}'

# move an application; the applicant is told on WhatsApp
curl -X PATCH localhost:8000/admin/applications/12 -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"status":"interview","note":"Tuesday 10am at our Westlands office"}'

# answer a ticket
curl -X POST localhost:8000/admin/tickets/3/reply -H "X-Admin-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"reply":"Fixed, your location is now Kisumu.","close":true}'

# list things
curl localhost:8000/admin/users -H "X-Admin-Key: $KEY"
curl "localhost:8000/admin/applications?status=applied" -H "X-Admin-Key: $KEY"
curl "localhost:8000/admin/tickets?status=open" -H "X-Admin-Key: $KEY"
```

Job alerts run from cron (or any scheduler that can call HTTP):

```bash
*/30 * * * *  cd /srv/chat-what && jobbot send-alerts        # or: POST /admin/alerts/run
```

Each run sends at most `ALERTS_PER_USER_PER_RUN` jobs per user, only jobs posted in the last
`ALERT_LOOKBACK_DAYS`, and never the same job twice. Inside the window the user gets one list
message; outside it one template per job.

Interactive API docs are at `/docs` when the server is running.

## Add a feature

1. Create `jobbot/bot/modules/<name>.py` with a `Module` subclass. Declare `entries` (menu rows),
   `commands` (the `action` prefixes of your button ids), optional `keywords` (typed words that
   open it) and `states` (only the states in which you asked the user to type something).
2. Implement `open(ctx, entry)`, `command(ctx, action, arg)` and, if you ask questions,
   `text(ctx, text)`. `ctx` gives you the db session, the user, and `ctx.text / ctx.buttons /
   ctx.list / ctx.menu` to reply. `ctx.set_state(state, data)` stores what you need between turns.
3. Register it in `build_bot()` in `jobbot/bot/engine.py`. The main menu renders buttons for up
   to three entries and a list for up to ten.
4. Drive it in `jobbot chat`, then add a test in `tests/test_bot_flows.py` using the `Chat`
   helper.

Nothing else changes. The FAQ module is 60 lines and is a good template.

## Layout

```
jobbot/
  config.py            settings from environment / .env
  models.py            User, ConversationState, Job, Application, SupportTicket, AlertSent, logs
  db.py                engine, sessions
  main.py              FastAPI app
  cli.py               init-db, seed, send-alerts, chat (terminal simulator)
  whatsapp/            messages.py (payload builders), client.py (Meta Cloud API), webhook.py (parsing, signature)
  bot/                 engine.py (core state machine), context.py, copy.py, modules/{jobs,faq,contact,settings}.py
  services/            jobs.py (scoring), applications.py, alerts.py, notify.py (24h-window aware sending)
  api/                 webhook.py, admin.py, deps.py
  data/faq.json        Help & FAQ content
tests/                 pytest suite with an in-memory db and a recording sender
```
