# Trace CRM — Engineering Guide

Onboarding guide for engineers working on this codebase. It describes the system **as it is built today**
(October 2026). Where older docs describe plans that were never built, this guide says so (see
[§14 Doc drift](#14-doc-drift-and-known-limitations)).

Related docs: [ARCHITECTURE.md](ARCHITECTURE.md) (original design and rationale), [DATABASE.md](DATABASE.md)
(schema/ERD), [`TASKS.md`](../TASKS.md) (change log of everything shipped, with verification notes).

---

## Contents

1. [What the product does](#1-what-the-product-does)
2. [Status at a glance](#2-status-at-a-glance)
3. [Running it locally](#3-running-it-locally)
4. [Architecture as built](#4-architecture-as-built)
5. [Repository map](#5-repository-map)
6. [Backend](#6-backend-appsapi)
7. [Core flows, end to end](#7-core-flows-end-to-end)
8. [Frontend](#8-frontend-appsweb)
9. [Database and migrations](#9-database-and-migrations)
10. [Configuration](#10-configuration)
11. [Security model](#11-security-model)
12. [Testing](#12-testing)
13. [Operations: backups, git, logs](#13-operations-backups-git-logs)
14. [Doc drift and known limitations](#14-doc-drift-and-known-limitations)
15. [How to make common changes](#15-how-to-make-common-changes)

---

## 1. What the product does

Trace CRM is a private, internal tool for Trace Studio: a **cold-email outreach platform plus a small sales CRM**.
Expected volume is 100–500 emails/day from a few connected Gmail mailboxes, used by a handful of people.

| Area | What a user does |
|---|---|
| **Prospects** | Import people from CSV/XLSX, dedupe, tag, filter, export (admin). Each has a timeline. |
| **Companies** | Group prospects by company. |
| **Campaigns + Sequences** | Multi-step email sequences with `{{variables}}`, sending windows, daily limits, mailbox rotation. |
| **Email Accounts** | Connect Gmail with OAuth; send test emails; pause/resume; see health. |
| **Unified Inbox** | Replies to campaign emails from all mailboxes in one place; reply from the app. |
| **Opportunities** | A kanban board of deals with custom stages; a slide-over panel with Overview, Tasks, Notes, Activity. |
| **Tasks + Notification Center** | Tasks with due times and reminders; a bell with in-app (and optional email) reminders. |
| **Analytics / Overview** | Sent/reply/bounce counts and funnels from an event ledger. |
| **Suppression List** | Addresses that must never be emailed (unsubscribes, bounces, manual). |
| **Settings** | Company, compliance footer, sending defaults, notifications. |

Two naming facts that will otherwise confuse you:

- **"Prospects" in the UI are `contacts` in the code and database.** Their status column is `lead_status`, and
  `/leads/*` URLs redirect to `/prospects/*`. The rename was UI-only on purpose.
- **"Opportunities" are a subset of prospects**: a row in `crm_opportunities` with `on_board = true`. Importing
  prospects does *not* create opportunities; the board starts empty and cards are added from the board.

---

## 2. Status at a glance

| Piece | State |
|---|---|
| Prospects, companies, import/export, tags | Built |
| Campaigns, sequences, templates, sending engine | Built |
| Gmail (OAuth, send, reply sync) | Built |
| Sandbox mailbox (dev-only fake provider writing `.eml` files) | Built |
| Unified Inbox (campaign replies only, hide/"delete") | Built |
| Opportunities CRM, stages, tasks, notes, activity | Built |
| Notification Center + reminder engine | Built |
| Analytics, Overview dashboard | Built |
| **AI Personalization**, **Website Audits** | **Placeholder pages only** (`components/not-available.tsx`). Tables exist (`ai_personalizations`, `website_audits`), no API or workers. |
| Microsoft 365 mailboxes | Not built (provider value exists in the schema; no adapter) |
| Docker / Caddy / production deployment | Not built (`infrastructure/` is empty). The app has never been deployed. |
| Security hardening (2FA, lockout, idle timeout, export controls, alerts) | Done on branch **`security-hardening`**, **not merged**: it needs one additive migration applied to the live database first. See [§11](#11-security-model). |

**Sending is gated.** `REAL_SENDING` is `test_only` on the owner's machine: real mail only goes to addresses in
`ALLOWED_TEST_RECIPIENTS`. Do not change this without the owner's explicit approval (see [§7.2](#72-the-real_sending-gate)).

---

## 3. Running it locally

The development machine is **Windows 10, no Docker**. Postgres runs embedded via the `pgserver` pip package.

### 3.1 Processes

| Process | Command (from repo root unless noted) | Port |
|---|---|---|
| Database | `apps/api/.venv/Scripts/python scripts/dev_db.py` (idempotent; stays running) | random, written to `apps/api/.env` |
| Backend API | in `apps/api`: `.venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8001` | 8001 |
| Engine (sending, reply sync, reminders) | in `apps/api`: `.venv/Scripts/python -u -m app.cli engine` | – |
| Frontend | in `apps/web`: `npm run build` then `npm run start -- -p 3000 -H 127.0.0.1` | 3000 |
| Frontend (live reload) | in `apps/web`: `npm run dev -- -H 127.0.0.1` | 3000 |

The browser only talks to `:3000`. Next.js rewrites `/api/*` and `/u/*` to the API (`API_URL` env var), so cookies
are same-origin and there is no CORS anywhere.

**VS Code:** `D:\Development\.vscode\tasks.json` defines **"Trace: start all"** (database → backup → backend,
engine, frontend; also works as "restart all") and "Trace: stop all". On the owner's machine it is bound to
**Ctrl+Alt+T**.

### 3.2 First-time setup

```bash
cd apps/api
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt
.venv/Scripts/python -m app.cli generate-keys   # prints SECRET_KEY and TOKEN_ENCRYPTION_KEYS for apps/api/.env
.venv/Scripts/python ../../scripts/dev_db.py --switch   # creates the DB and writes DATABASE_URL to .env
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m app.cli create-admin --email you@example.com --name "You"   # prompts for a password
cd ../web && npm install
```

`dev_db.py` refuses to repoint `apps/api/.env` at a different database unless `--switch` is given. This is a
deliberate guard: an earlier version silently moved the API to a stale copy of the data.

### 3.3 CLI (`app/cli.py`)

| Command | Purpose |
|---|---|
| `create-admin` | Create the first admin user (prompts for a password). |
| `generate-keys` | Print fresh `SECRET_KEY` / `TOKEN_ENCRYPTION_KEYS`. |
| `engine [--interval N] [--once]` | Run the background engine loop (default every 30 s). |
| `inbox-cleanup [--apply]` | Hide non-campaign conversations from the Unified Inbox (dry run by default; deletes nothing). |

---

## 4. Architecture as built

```
 Browser ──► Next.js (apps/web, :3000)
               │  pages + client components (React Query)
               │  rewrites /api/* and /u/*  ─────────────┐
               ▼                                          ▼
          (static/UI only)                  FastAPI (apps/api, :8001)
                                             routes → services → models
                                                 │            │
                                                 ▼            ▼
                                          PostgreSQL 16   Gmail API / Google OAuth
                                                 ▲
                                                 │ same code (services/*)
                                      Engine (python -m app.cli engine)
                                      every 30 s: activate scheduled campaigns →
                                      sync replies → send due steps → reconcile → reminders
```

- **One Python package** (`apps/api/app`) serves both the API and the engine. Business logic lives in
  `services/`; routes are thin.
- **Synchronous SQLAlchemy 2** everywhere. FastAPI runs sync endpoints in a threadpool.
- The **engine** is a single loop for development. `app/workers/celery_app.py` has the same steps as Celery beat
  tasks for a future production deployment with Redis; it is not used locally.
- Concurrency safety comes from **Postgres row locks and unique constraints**, not from a single-process
  assumption. Running two engines at once is safe by design.

---

## 5. Repository map

```
apps/
  api/                       Python backend (FastAPI + engine)
    app/
      main.py                App factory: middleware (rate limit, security headers), error handlers, routers
      cli.py                 Operator CLI (§3.3)
      core/                  Framework-level building blocks (§6.1)
      models/                SQLAlchemy ORM models (§6.2)
      schemas/               Pydantic request/response models
      api/deps.py            Auth dependencies: current_session, reader/writer/admin, audit()
      api/routes/            One router per area (§6.3)
      services/              Business logic (§6.4)
      providers/             Mail provider adapters: gmail, sandbox, MIME builder (§6.5)
      workers/               engine.py (dev loop), celery_app.py (prod, unused)
    alembic/versions/        Migrations (§9)
    tests/                   pytest suite against a real Postgres (§12)
    requirements.txt         Pinned runtime deps;  requirements-dev.txt adds pytest, ruff, pgserver
  web/                       Next.js 16 frontend (§8)
    src/app/                 App Router pages; (app)/ = signed-in area with the sidebar shell
    src/components/          App components + components/ui (shadcn-style primitives on Base UI)
    src/lib/                 api.ts (fetch wrapper), types.ts (API types), format.ts
    src/proxy.ts             Next middleware: redirects to /login when there is no session cookie
    next.config.ts           Rewrites to the API, redirects (/leads → /prospects), security headers/CSP
scripts/
  dev_db.py                  Start/stop embedded Postgres; guards which DB .env points at
  backup_db.py               Verified pg_dump backups to var/backups (§13)
docs/                        This guide, ARCHITECTURE.md, DATABASE.md, IMPLEMENTATION_PLAN.md
var/                         Git-ignored runtime data: pgdata/, backups/, logs/, sandbox outbox
TASKS.md                     Running log of what was built and how it was verified
```

---

## 6. Backend (`apps/api`)

Stack: Python 3.12, FastAPI 0.162, SQLAlchemy 2.0 (sync, psycopg 3), Alembic, Pydantic 2, argon2-cffi,
cryptography (Fernet), httpx, nh3 (HTML sanitiser), openpyxl + defusedxml.

### 6.1 `app/core/`

| File | What it does |
|---|---|
| `config.py` | `Settings` (pydantic-settings). Reads env vars and `apps/api/.env`. `get_settings()` is cached. `validate_for_runtime()` refuses to start in production with unsafe config. |
| `db.py` | Engine/session factory. `get_db()` is the FastAPI dependency; `session_scope()` is the commit-or-rollback context manager. |
| `security.py` | Argon2id password hashing, session creation (random token, only its SHA-256 is stored), `load_session()`, password strength rules. |
| `crypto.py` | `encrypt/decrypt` with MultiFernet (OAuth tokens at rest; key rotation = prepend a key). `sign/unsign` are HMAC tokens (unsubscribe links). |
| `ratelimit.py` | Fixed-window limiter. Redis if `REDIS_URL` is set, else in-process memory (fine for one API process). |
| `logging.py` | JSON logs to stdout with redaction of anything that looks like a token, secret, password or cookie. |

### 6.2 `app/models/` — the data model by domain

| File | Tables (model) |
|---|---|
| `core.py` | `users`, `sessions` (UserSession), `audit_logs`, `app_settings` (JSONB sections), `oauth_states` |
| `crm.py` | `companies`, `tags` + `contact_tags`, **`contacts`** (prospects), `crm_activities` (timeline), `pipeline_stages`, `crm_opportunities`, `crm_tasks`, `crm_notes` + `crm_note_revisions`, `notifications`, `website_audits`*, `ai_personalizations`* |
| `mail.py` | `email_accounts` (mailboxes), `campaigns`, `campaign_mailboxes`, `sequence_steps`, `email_templates`, `campaign_contacts` (enrollment), `scheduled_messages` (queue), `conversations`, `sent_messages`, `received_messages`, `email_events` (analytics ledger), `suppression_entries` |

\* schema only, unused.

Conventions: UUID primary keys (`UUIDPk`), `created_at/updated_at` (`Timestamps`), soft delete via `deleted_at`
on contacts, companies and campaigns, and status columns enforced with `CHECK` constraints. The status
vocabularies are constants at the top of `mail.py`/`crm.py`:

- Campaign: `draft → scheduled → active ⇄ paused → completed | stopped | error` (allowed moves:
  `services/campaigns.py: TRANSITIONS`)
- Enrollment (`campaign_contacts.status`): `active, replied, completed, unsubscribed, bounced, stopped, removed, error`
- Queue (`scheduled_messages.status`): `queued, sending, sent, failed, cancelled, unknown`
- Mailbox: `connected, paused, auth_error, error, disconnected`
- Prospect (`contacts.lead_status`): `new_lead, contacted, replied, interested, meeting_scheduled, proposal_sent, won, lost, not_interested`

### 6.3 `app/api/` — routes and permissions

Every route except login, the Google OAuth callback, `/api/health` and the public `/u/{token}` unsubscribe
pages requires a session. Every unsafe method (POST/PUT/PATCH/DELETE) also requires the `X-CSRF-Token` header.
Roles come from `api/deps.py`: **`reader`** (viewer+), **`writer`** (member+), **`admin`**.

| Router | Prefix | What's in it | Notable permission |
|---|---|---|---|
| `auth.py` | `/api/auth` | login/logout/me, password change, revoke sessions, user admin | user admin = admin |
| `dashboard.py` | `/api/dashboard` | Overview numbers | reader |
| `contacts.py` | `/api/contacts` | list/filter/sort, CRUD, bulk actions, notes, timeline, CSV/XLSX import (preview + commit), export | export = admin on the security branch (reader on main) |
| `companies.py` | `/api/companies` | CRUD + a company's prospects | |
| `campaigns.py` | `/api/campaigns`, `/api/templates` | campaigns, steps, enroll, preflight, previews, test send, templates | |
| `email_accounts.py` | `/api/email-accounts` | Gmail OAuth start/callback, sandbox mailboxes, pause/resume/sync, test email, disconnect | admin for all changes |
| `inbox.py` | `/api/inbox` | conversations, read/label, reply, hide ("delete") | |
| `opportunities.py` | `/api/opportunities`, `/api/notes` | stages (admin), board, create/remove/move, detail, tasks, notes + revisions, activity, task types | stage management = admin |
| `crm.py` | `/api` | Older endpoints used by prospect pages: `/pipeline`, `/contacts/{id}/opportunity`, `/tasks` | |
| `notifications.py` | `/api/notifications` | the current user's notifications (always filtered by `user_id`) | any signed-in user, own rows only |
| `suppressions.py` | `/api/suppressions` | list/add/remove | removal = admin |
| `analytics.py` | `/api/analytics` | filtered stats | |
| `settings.py` | `/api/settings` | sections from `app_settings` + integration status | changes = admin |
| `unsubscribe.py` | `/u/{token}` | public HTML confirm page (GET) and unsubscribe (POST, also RFC 8058 one-click) | public, HMAC token |

`audit(db, user, action, entity_type, entity_id, request, **data)` in `deps.py` writes an `audit_logs` row. Use
it for anything security-relevant.

### 6.4 `app/services/` — business logic

| Service | Responsibility |
|---|---|
| `campaigns.py` | Campaign state machine (`transition`, `start`, `pause`, `resume`, `stop`), `preflight` checks, `enroll_contacts`, stop primitives used by replies/unsubscribes (`stop_contact_everywhere`, `stop_email_everywhere`, `mark_replied`), `replace_steps`, `duplicate`, `stats`. |
| `sending.py` | **The sending engine** (§7.1): `send_due` → `dispatch_due` → `send_scheduled` → `record_success`; `reconcile_unknown`; mailbox choice (`pick_mailbox`), daily limits, footer/unsubscribe URL, `test_recipients`. |
| `scheduling.py` | Sending windows, business-day delays and time zones (`in_window`, `next_window_start`, `add_delay`). |
| `rendering.py` | Template rendering: `{{var}}` / `{{var|fallback}}`, no expressions, HTML-escaped. Raises `MissingVariables` instead of sending a broken email. |
| `enrollment.py` | Read-only dry runs: missing-variable reports and per-prospect previews before enrolling. |
| `inbound.py` | **Reply sync** (§7.3): parse MIME, classify, match to a sent message, apply effects under a row lock. `sync_mailbox` is the entry point. |
| `inbox.py` | Unified Inbox visibility: only campaign conversations are shown; "delete" hides (soft) and stays hidden across syncs. |
| `google_oauth.py` | OAuth authorize URL (PKCE + state), code exchange, token storage (encrypted), refresh under a row lock, revoke. |
| `contacts.py` | Prospect helpers: activity timeline, suppression lookups, tags/companies get-or-create, `set_lead_status`, `template_variables`. |
| `csv_import.py` | CSV/XLSX parsing (encoding/dialect sniffing, zip-bomb cap, formulas never evaluated), column mapping suggestions, transactional import with per-row report, dedupe, CSV export with formula-injection escaping. |
| `normalize.py` | Email/website/state normalisation, timezone guess from US state. |
| `opportunities.py` | Board: stages CRUD/reorder/delete (with reassignment), card column resolution, create/remove/move, detail payloads. |
| `tasks.py` | Opportunity tasks: due time in the task's own timezone, reminder minutes, complete/reopen, history. |
| `notes.py` | Notes with edit history (`crm_note_revisions`) and archive. |
| `reminders.py` | Engine step: fire due reminders and overdue notices into `notifications` exactly once; optional email copy. |
| `notify_email.py` | Plain SMTP, only to the notification address in Settings. Deliberately separate from campaign mailboxes. |
| `analytics.py` | Stats from the `email_events` ledger. "Accepted" by the provider, never "delivered". |
| `app_settings.py` | JSONB settings sections merged over `DEFAULTS`, validation, `compliance_ready()` (blocks campaigns until a postal address and company name exist). |

### 6.5 `app/providers/` — mail adapters

- `base.py` — `MailProvider` interface (`send`, `list_new`, `fetch_raw`, `find_by_message_id`, …), the
  `OutgoingMessage`/`SendResult`/`InboundRaw` types and the error taxonomy that drives retries:
  `TemporaryProviderError` (back off), `PermanentProviderError` (fail), `ProviderAuthError` (mailbox → `auth_error`),
  `AmbiguousSendError` (timeout after the request may have landed → status `unknown`, reconciled later).
- `gmail.py` — Gmail REST API (`messages.send`, `history.list`, `messages.get`). Scopes: `gmail.send` +
  `gmail.readonly` only.
- `sandbox.py` — dev-only. "Sends" by writing `.eml` files under `SANDBOX_OUTBOX_DIR`; can simulate replies and
  bounces (`/api/email-accounts/{id}/sandbox/simulate`). Disabled in production.
- `mime.py` — builds RFC 5322 messages. Every send carries **our own Message-ID**, which is what makes threading,
  reply matching and reconciliation work.
- `__init__.py` — `get_provider(account, purpose)` and **`send_block_reason`**, the `REAL_SENDING` gate (§7.2).

---

## 7. Core flows, end to end

### 7.1 Campaign → email sent

1. A user builds a campaign (steps, mailboxes, schedule, limits) and enrolls prospects. `preflight()` blocks
   starting while compliance settings, mailboxes or template variables are missing.
2. Enrollment creates `campaign_contacts` rows with `next_send_at`.
3. Every engine tick, `sending.send_due()`:
   - **dispatch**: selects due enrollments with `FOR UPDATE SKIP LOCKED` and inserts a `scheduled_messages` row.
     `(campaign_contact_id, sequence_step_id)` is unique, so a step can never be queued twice.
   - **reservation** (short transaction): locks message → enrollment → mailbox; re-checks campaign status,
     suppression, reply status, mailbox health/circuit breaker, sending window, daily limits and spacing; renders
     the template; marks `sending`; commits.
   - **send**: provider call with **no transaction open**.
   - **result**: `sent_messages` + `email_events` rows, advance to the next step and compute its time (business
     days, recipient timezone, window). Errors back off, fail, or flag the mailbox, by error type.
4. `reconcile_unknown()` resolves `unknown` sends by searching the provider for our Message-ID.

Ordering matters: the engine **syncs replies before sending**, and reply processing locks the same enrollment
row, so a reply that has been processed always cancels the pending follow-up.

**Video on a step** (`services/video_assets.py`, `routes/video_assets.py`). A step can use one library video as a
clickable preview (`video_preview`), a real MP4 attachment (`video_attach`), or both.

- Bytes live in Postgres (`video_asset_chunks`), uploaded in 3 MiB pieces: the hosted API has no persistent disk and
  a 4.5 MB request limit. No route returns them; they only leave as an email attachment.
- The preview is an inline `cid:` thumbnail linking to `video_assets.link_url`, an external page, because the stored
  file is private.
- `providers/mime.py` holds each provider's message limit. The configurable limit (Settings → Video attachments) is
  capped by it: 25 MB per message is about 17.7 MB per file after base64.
- `preflight()` blocks a campaign whose video is over the limit or whose preview has no link. If that becomes true
  mid-campaign, messages wait and retry hourly with the reason in `last_error`. An email never goes out without
  the video it promises.
- Gmail attachment messages use the resumable upload endpoint; everything else keeps the JSON `raw` call. SMTP also
  honours the server's announced `SIZE`, and its IMAP Sent copy omits the file to save mailbox quota.
- `send_due` stops starting new sends after `SEND_BUDGET_SECONDS`, so slow uploads can't hit the 300 s function limit.

### 7.2 The `REAL_SENDING` gate

`providers/__init__.py: send_block_reason(account, purpose, to_email)` is checked **per message, with its
recipient, inside the Gmail adapter at the moment of sending**:

| `REAL_SENDING` | Behaviour for real (non-sandbox) mailboxes |
|---|---|
| `disabled` (default) | Nothing sends. Reply sync still works. |
| `test_only` | Every purpose (`campaign`, `reply`, `test`) works, but **only to `ALLOWED_TEST_RECIPIENTS`**. |
| `enabled` | Normal sending. |

Sandbox mailboxes bypass the gate (they never leave the machine) and are disabled in production.

### 7.3 Reply → inbox, CRM, sequence stop

`inbound.sync_mailbox(account_id)` runs every 2 minutes per mailbox (engine) or on demand (Sync button):

1. Gmail `history.list` since the stored cursor → fetch raw MIME → `parse()`.
2. `classify()`: bounce (DSN), auto-reply, unsubscribe request, or human reply; `intent_of()` labels interest.
3. `match()`: provider thread id → `In-Reply-To/References` against our Message-IDs → sender address.
4. `process()` under a row lock: store `received_messages` (HTML sanitised with `nh3`), mark the enrollment
   replied and cancel queued steps, suppress bounces and unsubscribes, advance `lead_status`, log timeline activity.
5. Only conversations started by campaign mail appear in the Unified Inbox (`inbox.visible`).

### 7.4 Opportunities board

- Column for a card = `coalesce(crm_opportunities.stage_id, stage linked to the prospect's lead_status, first stage)`.
- Only `on_board = true` opportunities are shown; "Remove" takes a card off the board without deleting the prospect.
- "New opportunity" reuses an existing prospect with the same email instead of creating a duplicate.
- Stages are admin-managed (`pipeline_stages`); deleting a stage reassigns its cards.

### 7.5 Tasks → reminders → bell

`reminders.tick()` (every engine pass) claims due tasks with `FOR UPDATE SKIP LOCKED`, stamps them in the same
transaction, and inserts notifications that are unique per `(user, task, kind, fire_at)`. The result is
exactly-once delivery, even with multiple engines or a crash mid-pass. Overdue notices fire 15 minutes after the
due time. The frontend bell (`components/notification-bell.tsx`) polls `/api/notifications/unread-count` every
30 s; desktop alerts are opt-in.

### 7.6 Import / export

- Import is two calls: `/api/contacts/import/preview` (headers, sample rows, suggested mapping) then
  `/api/contacts/import` (with `dry_run` support). The whole import is one transaction, with a per-row report.
  Re-imports of the same file are detected by SHA-256.
- Export streams CSV with spreadsheet formula injection neutralised (`_csv_safe`).

### 7.7 Compliance / unsubscribe

Every campaign email gets the footer from Settings → Compliance (it must contain `{{physical_address}}` and
`{{unsubscribe_url}}`) and `List-Unsubscribe` + `List-Unsubscribe-Post` headers. `/u/{token}` shows a
confirmation page (GET, so link scanners don't unsubscribe people); POST suppresses the address globally and
stops it in every campaign.

---

## 8. Frontend (`apps/web`)

Stack: Next.js 16 (App Router, Turbopack), React 19, TypeScript, Tailwind CSS v4, shadcn-style components on
**Base UI** (`components/ui/*`), TanStack Query 5, react-hook-form + zod 4, dnd-kit (board drag-and-drop),
recharts, sonner (toasts), lucide icons, next-themes (dark mode).

### 8.1 Structure

- `src/app/login/page.tsx` — sign-in.
- `src/app/(app)/layout.tsx` wraps every signed-in page in **`components/app-shell.tsx`**: sidebar (grouped nav
  `NAV_GROUPS`, collapsible with Ctrl+B), header with the notification bell, and `useMe()` (current user).
  `useCanWrite()` / `useIsAdmin()` hide controls, but **the API is the real authority**.
- Pages: `page.tsx` (Overview), `opportunities`, `prospects` (+ `[id]`, `import`), `companies`, `campaigns`
  (+ `[id]` with tabs), `sequences`, `inbox`, `email-accounts`, `analytics`, `suppressions`, `settings`,
  and the placeholders `ai` and `audits`.
- `src/proxy.ts` — Next middleware. If there is no `ts_session` cookie, it redirects to `/login?next=…`. This is an
  optimisation only; it does not authorise anything.

### 8.2 Data access

- **`lib/api.ts`** is the only way to call the backend: same-origin `fetch`, JSON in and out, adds `X-CSRF-Token`
  (read from the `ts_csrf` cookie) on unsafe methods, throws `ApiError` (with field errors), and redirects to
  `/login` on a 401.
- Server state lives in **React Query** (`useQuery`/`useMutation`); invalidate the relevant `queryKey` after a
  mutation. There is no global client store.
- `lib/types.ts` hand-mirrors the API response shapes. Keep it in sync with `app/schemas/*` when you change an
  endpoint.

### 8.3 Notable components

| Component | Notes |
|---|---|
| `opportunities/*` | Board pieces: `opportunity-panel.tsx` (slide-over with tabs), `overview/tasks/notes/activity-tab.tsx`, `stage-manager.tsx`, `new-opportunity.tsx`, `shared.tsx` (formatters, due labels). |
| `campaign/sequence-editor.tsx`, `campaign/campaign-tabs.tsx` | Campaign builder. |
| `email-frame.tsx` | Renders email HTML in a **sandboxed iframe** (no scripts) with its own CSP; remote images stay blocked until the user clicks "Show images". |
| `enroll-dialog.tsx`, `schedule-editor.tsx`, `lead-form.tsx`, `company-form.tsx` | Shared forms. |
| `common.tsx` | `PageHeader`, `Field`, `EmptyState`, `ErrorState`, `ConfirmDialog`, `Pagination`, `StatusBadge`, `Spinner`. |

Styling uses design tokens in `app/globals.css` (light and dark). Use the existing tokens (`text-signal`,
`bg-muted`, …) rather than raw colours.

---

## 9. Database and migrations

- PostgreSQL 16. Schema details and ERD: [DATABASE.md](DATABASE.md) (the ERD predates the CRM additions:
  `pipeline_stages`, `crm_notes`, `crm_note_revisions`, `notifications`).
- Migrations are in `apps/api/alembic/versions`. Current chain:
  `4302a2533a84` initial → `b4004444182d` conversation_hidden → `3863ec385a80` opportunities_crm (seeds 9 stages)
  → `10ed919f7ab4` opportunities_on_board → (branch only) `5ca204ddd0e5` security_hardening.
- Create one with `alembic revision --autogenerate -m "..."`, then **read and edit it**. Autogenerate misses
  CHECK constraints, triggers and data seeding.
- The test suite runs every migration from scratch on each run, so a broken migration fails the tests.
- **House rule:** migrations must be additive, or approved by the owner first. Never run `downgrade`, drop
  data, or point the app at a different database without explicit approval. Take a backup first
  (`scripts/backup_db.py`).

---

## 10. Configuration

All backend config is environment variables (or `apps/api/.env`, which is git-ignored). There is no
`.env.example` yet. These are the variables that matter:

| Variable | Purpose / default |
|---|---|
| `APP_ENV` | `development` (default) / `test` / `production`. Production turns off API docs and sandbox mailboxes and runs strict config checks. |
| `DATABASE_URL` | `postgresql+psycopg://…`; written by `dev_db.py` locally |
| `SECRET_KEY` | ≥ 32 chars; HMAC for unsubscribe tokens (and 2FA challenges on the security branch) |
| `TOKEN_ENCRYPTION_KEYS` | Comma-separated Fernet keys. The first encrypts, all decrypt (rotation). Losing them = reconnect every mailbox. |
| `PUBLIC_URL` | Base URL for OAuth redirects and unsubscribe links (`http://localhost:3000`) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | Gmail OAuth (redirect defaults to `{PUBLIC_URL}/api/email-accounts/oauth/google/callback`) |
| `REAL_SENDING` | `disabled` / `test_only` / `enabled` (§7.2) |
| `ALLOWED_TEST_RECIPIENTS` | Comma-separated allowlist for `test_only` |
| `COOKIE_SECURE` | Must be `true` behind HTTPS |
| `TRUST_PROXY_HEADERS` | Only `true` behind your own reverse proxy (otherwise `X-Forwarded-For` is ignored) |
| `SESSION_TTL_HOURS`, `LOGIN_RATE_LIMIT_PER_MINUTE`, `API_RATE_LIMIT_PER_MINUTE` | Session and rate-limit tuning |
| `REDIS_URL` | Optional: shared rate limiter and Celery broker |
| `NOTIFY_SMTP_HOST/PORT/USERNAME/PASSWORD/FROM/STARTTLS` | Optional SMTP for reminder emails |
| `SANDBOX_OUTBOX_DIR` | Where sandbox "sent" mail goes (`./var/sandbox_outbox`) |
| `CSV_MAX_BYTES`, `CSV_MAX_ROWS` | Import limits (10 MB / 25,000 rows) |
| `DEV_DB_PGDATA` | Written by `dev_db.py`; records which data directory `DATABASE_URL` belongs to |

The frontend reads only `API_URL` (where to proxy `/api/*`, default `http://127.0.0.1:8000`; the local tasks set
8001). Workspace settings (company, compliance, sending defaults, notifications, task types) live in the
`app_settings` table and are edited in the UI.

---

## 11. Security model

**On `main` (live today):**

- Sessions: random token in an `HttpOnly`, `SameSite=Lax` cookie; only its SHA-256 is stored; 12-hour TTL;
  revoked on logout, password change and user deactivation. `load_session` re-checks `is_active` on every request.
- CSRF: a per-session token, readable `ts_csrf` cookie, required as `X-CSRF-Token` on every unsafe method, compared
  in constant time.
- Passwords: Argon2id, rehashed on login when parameters change. Login is rate limited per IP and per email, and
  timing is equalised for unknown users.
- RBAC: viewer < member < admin, enforced server-side by dependencies on every route.
- Secrets: OAuth tokens Fernet-encrypted at rest and never serialised; logs redact secrets.
- Input/output: ORM-only SQL (no string-built queries; sort columns whitelisted); templates can't execute code;
  inbound HTML sanitised with `nh3` and shown in a sandboxed iframe; CSV exports escaped against formula injection;
  XLSX parsed with defusedxml and a decompression cap.
- Headers: `nosniff`, `X-Frame-Options: DENY`, CSP on API responses and in `next.config.ts`, HSTS when
  `COOKIE_SECURE`. API docs (`/api/docs`, `/api/openapi.json`) are disabled in production.

**On branch `security-hardening` (reviewed and tested, not merged):**

- **Two-step verification** (TOTP + 10 single-use recovery codes): `app/core/mfa.py`,
  `components/security-settings.tsx`. Sign-in becomes password → signed 5-minute challenge → code.
  `REQUIRE_ADMIN_MFA` (on by default in production) locks admin routes until the admin enrols.
- Account lockout: 8 failures per email (24 per IP) in 15 minutes → locked for 15 minutes.
  Sessions also expire after 120 idle minutes.
- Export is admin-only, 10 per hour, logged with its filters, and alerts admins.
- **Security alerts** go to admins through the Notification Center (`services/security_alerts.py`,
  `kind = "security"`).
- `audit_logs` is append-only at the database level (trigger); there's a Security log in Settings.
- The per-IP API rate limit is added, the frontend CSP is tightened, the production start-up check is stricter,
  and backups can be encrypted.
- `tests/test_security.py` sweeps **every route** for session, role and CSRF enforcement. A new route that
  forgets its auth dependency fails the suite.

To ship it: back up the database, run `alembic upgrade head` (one additive migration), merge into `main`, and
restart. The owner has not yet approved this; ask them first.

**Open items:** the local embedded Postgres uses the `postgres` superuser with `trust` auth on 127.0.0.1 (fine
for a single-user dev PC; production needs a dedicated role with a password). Backups are unencrypted until a
`BACKUP_ENCRYPTION_KEY` is set and stored off-machine.

---

## 12. Testing

```bash
cd apps/api
.venv/Scripts/python -m pytest -q          # ~2.5 min; uses an embedded Postgres in %TEMP%, never the dev DB
.venv/Scripts/python -m ruff check app tests alembic
cd ../web && npx tsc --noEmit && npx eslint src && npm run build
```

- `tests/conftest.py` starts a throwaway Postgres (`%TEMP%/ts_outreach_pgtest`), drops the schema, and runs all
  migrations. Tables are truncated after each test; reference stages are re-seeded.
  - Fixtures: `client` (a signed-in admin with CSRF handled), `make_user()`, `login()`, `compliance`.
- No real email provider is contacted: Gmail HTTP is mocked per test (`google_oauth.http_client` is the seam).
- Files: `test_auth`, `test_contacts`, `test_import_enroll`, `test_sending`, `test_gmail`, `test_inbox_filtering`,
  `test_crm_inbox_misc`, `test_opportunities`, `test_dashboard`, and `test_security` (branch).
- Counts: 101 tests on `main`, 122 on `security-hardening`.
- **Windows gotcha:** if pytest fails with `could not open directory "pg_notify"`, Windows temp cleanup deleted
  the test database's empty folders. Recreate the empty directories (`pg_notify`, `pg_tblspc`, `pg_twophase`,
  `pg_snapshots`, `pg_replslot`, `pg_commit_ts`, `pg_stat_tmp`, `pg_logical/snapshots`, `pg_logical/mappings`,
  `pg_dynshmem`, `pg_serial`) under `%TEMP%/ts_outreach_pgtest`.

---

## 13. Operations: backups, git, logs

- **Backups:** `scripts/backup_db.py` writes `var/backups/outreach-YYYYMMDD-HHMMSS.dump` (pg_dump custom format),
  verifies it with `pg_restore --list`, and never deletes anything. "Trace: start all" takes one automatically.
  `--to <folder>` writes elsewhere. To restore (destructive, ask first):
  `pg_restore --clean --if-exists --no-owner -d <url> <file>.dump`.
- **Git:** private repo `github.com/travieso999/trace-crm`. Branch `main` = what runs; `security-hardening` =
  pending work. Secrets, the database, backups and logs are git-ignored; keep it that way.
- **Logs:** JSON lines on stdout. The local VS Code tasks tee them to `var/logs/*.log`. Every request has an
  `X-Request-ID`.

---

## 14. Doc drift and known limitations

`ARCHITECTURE.md` was written before implementation. Differences from reality:

| ARCHITECTURE.md says | Reality |
|---|---|
| Caddy + Docker Compose topology, `infrastructure/` with Dockerfiles | Not built; `infrastructure/` is empty. |
| Celery worker + beat with Redis | `celery_app.py` exists but is unused; the dev engine loop does the work. |
| Microsoft Graph adapter | Not built. |
| AI adapters, SSRF-safe website fetcher, website audits | Not built (placeholder pages). |
| `docs/SECURITY.md` | Doesn't exist; see §11 and `TASKS.md`. |
| `scripts/` backup/restore | `backup_db.py` exists; restore is a documented manual `pg_restore`. |

Other limitations:

- The in-process rate limiter assumes one API process; set `REDIS_URL` if you run more.
- Behind the Next.js proxy, every client shares one IP for rate limiting unless a real reverse proxy sets
  `X-Forwarded-For` and `TRUST_PROXY_HEADERS=true`.
- Gmail's `gmail.readonly` is a Google "restricted" scope. More than 100 users, or a public launch, needs Google's
  verification and security assessment.
- `lib/types.ts` is maintained by hand (no OpenAPI codegen).

---

## 15. How to make common changes

**Add an API endpoint**
1. Put the logic in a function in `app/services/<area>.py` (testable without HTTP).
2. Add the route in `app/api/routes/<area>.py` with the right dependency: `Depends(reader|writer|admin)`.
   Never leave a route without one unless it is meant to be public.
3. Add Pydantic models in `app/schemas/` for anything non-trivial; never return ORM objects with secret columns.
4. Call `audit(...)` if it changes security-relevant state.
5. Add tests in `apps/api/tests/`; on the security branch, `test_security.py` automatically checks the new route
   for session/role/CSRF.
6. Mirror the response type in `apps/web/src/lib/types.ts`.

**Add a database column/table**
1. Change the model in `app/models/`.
2. `alembic revision --autogenerate -m "…"`; review it, keep it additive, give new non-null columns a server default.
3. Run the test suite (it applies all migrations from scratch).
4. Back up, then `alembic upgrade head` on the dev DB, with the owner's approval.

**Add a page**
1. Create `apps/web/src/app/(app)/<name>/page.tsx` (client component; fetch with `useQuery` + `api()`).
2. Add it to `NAV_GROUPS` in `components/app-shell.tsx`.
3. Reuse `PageHeader`, `Field`, `EmptyState`, `ErrorState` and `components/ui/*`.

**Add an engine step**
1. Write an idempotent function that claims work with `FOR UPDATE SKIP LOCKED` (see `reminders.py`).
2. Call it from `workers/engine.py: tick()` and add the matching task to `workers/celery_app.py`.

**Never, without the owner's explicit approval:** change `REAL_SENDING`, send real email outside the allowlist,
disconnect or re-authorise Gmail, run destructive SQL or migrations, switch the database, or commit secrets.
