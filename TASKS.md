# TASKS — running implementation checklist

Legend: [x] done & verified · [~] implemented, awaiting external verification · [ ] not started

## Phase A — Architecture
- [x] Workspace inspected (empty; Python/Git installed via winget; Docker pending — user installing)
- [x] docs/ARCHITECTURE.md, docs/DATABASE.md, docs/IMPLEMENTATION_PLAN.md, TASKS.md

## Phase B — Backend core
- [x] Config, logging, security primitives
- [x] Models + initial Alembic migration
- [x] Auth (login/logout/me, sessions, CSRF, RBAC, rate limit)
- [x] Contacts CRUD, search/filter, bulk, CSV import/export
- [x] Companies CRUD
- [x] Dashboard API
- [x] Verified 2026-10-26: pytest 20 passed (real Postgres), alembic upgrade/downgrade/upgrade + `alembic check` clean, ruff clean

## Phase C — Frontend
- [x] Next.js scaffold: app shell, login page, API client, types, shadcn/ui components
- [x] Vertical slice: Overview dashboard (KPIs, sent/replies charts, funnel, mailbox health, upcoming, activity),
      Leads (search/filter/sort/paging, bulk status/verification/tags/delete, create, export), lead detail (edit, notes,
      timeline), CSV import wizard (preview, mapping, custom fields, dry run), Companies list + detail (create/edit/delete)
- [x] Verified 2026-10-26: tsc, eslint, `next build` clean; browser-driven flows (login redirect, create/edit/note,
      bulk tag, filters, search, import dry-run + commit, company edit) pass against the real API; light/dark × desktop/phone
      screenshots reviewed, no horizontal overflow
- [x] Branding (2026-10-26): Trace palette (ink #0F0F0D / paper #F4F1E9 / signal #FF5A33) as theme tokens in
      globals.css, traced logo (components/brand.tsx, public/brand/*.svg), favicon/icon/apple-icon. Verified: tsc,
      eslint, `next build` clean; pytest 20 passed; light/dark × desktop/phone screenshots, no overflow, no console errors
- [x] Pages (2026-10-26): Campaigns list + detail (overview/launch checklist, sequence editor with live preview, variables,
      templates, test send; enrolled leads + add by filter; send queue/history; settings incl. schedule, mailboxes,
      missing-variable policy), Email Accounts (sandbox connect, limits/window/signature, pause/resume/sync/disconnect),
      Sequences (all sequences + template library), Pipeline (drag-and-drop incl. keyboard, deal value/meeting, tasks),
      Unified Inbox (views, search, sandboxed HTML, labels, read state, reply), Suppression List, Analytics, Settings,
      sidebar unread badge, Leads bulk "Add to campaign". AI Personalization / Website Audits show an honest
      "not available yet" state with live integration status.
- [x] Verified 2026-10-26: tsc, eslint, `next build` clean; headless-browser E2E against the real API + Postgres
      (start campaign → engine sends via sandbox → simulate reply → inbox auto-read + reply → deal saved → keyboard drag
      between stages → suppression add), unsubscribe link via the Next proxy; light/dark × desktop/phone, no overflow,
      no console errors
- [x] Spreadsheet import + enrollment (2026-10-27): CSV and XLSX upload (old .xls rejected with a clear message);
      header recognition for Client/Contact/Owner Name, First/Last, Business Name/Agency/Company, Email Address,
      Website, Phone, City, State; conservative full-name split that keeps the original value and never fills in
      missing names or companies (business-looking name columns are not used as a person's name); editable mapping;
      dry-run report per row (ready / missing merge fields / duplicate in file / already in list / invalid or missing
      email) plus a warning when the same file was imported before; imports into Leads + Companies. Enrollment:
      `dry_run` and `only_ready` options on `POST /campaigns/{id}/contacts`, per-lead personalized preview
      `GET /campaigns/{id}/preview/{contact_id}`, enroll dialog with report in the Leads bulk bar, Preview button on the
      campaign Leads tab, add-by-filter skips leads missing merge fields. Suppression, limits, unsubscribe and
      REAL_SENDING unchanged (enforced by the existing engine)
- [x] Verified 2026-10-27: pytest 72 passed (20 new in tests/test_import_enroll.py); ruff, tsc, eslint, `next build`
      clean; headless-browser XLSX import (dry run → commit → enroll with report → per-lead preview) against the real
      API with REAL_SENDING=disabled; report readable on desktop and phone, no overflow, no console errors

## Phase D/E — Sending engine (sandbox) — partly pulled forward
- [x] Provider interface + Sandbox provider (writes .eml, inbound via local inbox folder); Gmail/Microsoft raise
      ProviderUnavailable
- [x] Engine: dispatch (FOR UPDATE SKIP LOCKED, unique step guard), reservation checks (campaign/lead state,
      suppression, mailbox status + circuit breaker, joint sending windows incl. recipient TZ, mailbox + campaign daily
      limits, spacing, missing variables), threading (Re:, In-Reply-To/References, provider thread), List-Unsubscribe +
      one-click, compliance footer, business-day follow-ups, retry/backoff, ambiguous-send reconcile, completion
- [x] Inbound: parse, classify (bounce DSN / auto-reply / unsubscribe request / reply + keyword intent), match
      (thread → References → DSN recipient → sender), effects (stop sequence, suppress, CRM stage, events, activity)
- [x] Public unsubscribe pages `/u/<token>` (HMAC token, confirm page, RFC 8058 one-click POST)
- [x] APIs: email-accounts, campaigns (+ templates, preview, test-send), inbox, pipeline/opportunities/tasks,
      suppressions, analytics
- [x] Runner: `python -m app.cli engine` (single process, dev); Celery app + beat schedule in app/workers/celery_app.py
- [x] Verified 2026-10-26: pytest 36 passed (16 new: full sequence threading/completion, reply stops follow-ups,
      bounce/auto-reply/unsubscribe reply + link, missing-variable block + fallback, daily limit + window deferral,
      pause/resume/stop/duplicate, mailbox pause, RBAC, test-send allowlist, pipeline/tasks, suppressions, templates,
      analytics); ruff clean; `alembic check` clean (no schema changes)
- [~] Celery worker with Redis: wired, not run (no Redis locally)
- [x] Gmail OAuth (2026-10-26): authorization code + PKCE (S256), single-use 10-min state bound to the signed-in
      admin, scopes gmail.send + gmail.readonly only, callback `/api/email-accounts/oauth/google/callback` (via the
      Next proxy), Fernet-encrypted tokens, refresh under row lock (persisted), invalid_grant → auth_error +
      Reconnect, disconnect revokes at Google and deletes tokens. Gmail adapter: send (threadId), find_sent
      (rfc822msgid), reply sync via history.list from connect time (expired cursor → resume from now), error mapping
      (401 refresh-retry, 403 auth, 429/5xx retry, read timeout → reconcile). UI: Connect Gmail / Reconnect, clear
      callback error messages, sending-mode banner.
- [x] Real-sending kill switch `REAL_SENDING` = disabled (default) | test_only | enabled, enforced at mailbox
      selection, campaign preflight, send reservation and inside the Gmail adapter; sandbox unaffected.
      2026-10-27: test_only allows test emails, campaigns and inbox replies from a real mailbox, but only to
      ALLOWED_TEST_RECIPIENTS — preflight refuses to start if any enrolled lead is outside it, reservation defers such
      leads, and the Gmail adapter checks every message's recipient
- [x] Verified 2026-10-26: pytest 52 passed (16 new Gmail tests against an in-process fake Google: URL/PKCE/scopes,
      callback success + 6 error paths, session binding + state replay, refresh/revocation/reconnect, disconnect
      revoke, sending disabled/test_only/enabled, threading, reply sync, cursor reset, 429 retry); ruff, tsc, eslint,
      `next build` clean; browser check of Connect Gmail with Google blocked (no request before the click) and the
      cancel/replay callback messages through the Next proxy
- [~] Gmail against real Google: awaiting your OAuth client + a controlled test send
- [x] Mailbox "Send test email" (2026-10-27): `POST /api/email-accounts/{id}/test-email` (+ `GET …/test-recipients`),
      admin only, fixed message, "test" purpose (the only one REAL_SENDING=test_only allows), no campaign/lead. Test
      emails from a real mailbox now go only to ALLOWED_TEST_RECIPIENTS (app users no longer implicitly allowed;
      sandbox unchanged) — campaign test-send too. A successful sync clears a stale sync error (`last_error`)
- [x] Unified Inbox = campaign replies only (2026-10-27): sync stores a message only when it answers campaign mail
      (sequence step or in-campaign inbox reply; not test sends) by provider thread, In-Reply-To/References or bounce
      recipient — never by sender address alone. Unrelated mail is not stored; a lead writing in directly is not
      shown but still stops their active sequences / is suppressed on an unsubscribe request. Soft delete
      (`conversations.hidden_at/hidden_reason/hidden_by_user_id`, migration b4004444182d): `POST /api/inbox/delete`
      (bulk), delete in the conversation view, stays hidden after syncs; messages, events, analytics kept. Existing
      inbox: `python -m app.cli inbox-cleanup [--apply]` hid 6 non-campaign conversations on the dev DB
- [x] Verified 2026-10-27: pytest 80 passed (tests/test_inbox_filtering.py, 3 new Gmail test-email tests); ruff,
      `alembic check`, tsc, eslint, `next build` clean
- [x] SMTP + IMAP mailboxes (2026-10-27), for Hostinger and other password-based email:
      - `POST /api/email-accounts/smtp` checks the SMTP and IMAP logins, then stores the password Fernet-encrypted
        (Hostinger preset: smtp.hostinger.com:465, imap.hostinger.com:993)
      - Sends over SMTP with a copy in the IMAP Sent folder; reads replies from INBOX without marking them read
        (UID cursor from connect time); matches replies by In-Reply-To/References
      - A failure during SMTP DATA counts as sent on reconcile rather than risk a duplicate
      - A rejected password means auth_error and a Reconnect button
      - Migration 5cbac32cf346
      - Verified: pytest 115 passed (9 new in tests/test_smtp.py); ruff, tsc, eslint and `next build` clean
      - Live on production: migration applied, providers endpoint and validation checked
- [ ] Microsoft Graph adapter + OAuth
- [ ] Reply/mailbox-error email notifications (settings saved, not sent yet)

## Phase F — not started
- [ ] AI personalization (generation, evidence, approval → {{custom_intro}}), AI reply classification
- [ ] Website audits (SSRF-safe fetcher, checks, batch runs)

## Running locally
- API must be restarted after pulling these changes (new routers). The engine is a separate process:
  `apps/api/.venv/Scripts/python -m app.cli engine` (sends only through sandbox mailboxes in this build).

## Opportunities CRM + Notification Center (2026-10-27)
- [x] Pipeline renamed Opportunities (`/pipeline` → 308 → `/opportunities`; old `/api/pipeline` kept)
- [x] Customizable stages (`pipeline_stages`, migration 3863ec385a80 seeds the 9 previous columns): rename, color,
      add, drag-reorder, delete with "move opportunities to" (admins). A stage can be linked to a lead status: moving
      a card there sets it (Won/Not interested keep their automation); automation moves cards in linked stages only;
      cards in unlinked stages stay put. Deleting never removes leads/opportunities or changes lead statuses
- [x] Board cards: client, business, email, phone, stage, next task due (overdue in red), deal value
- [x] Slide-over panel (~72vw, full screen on phone, URL `?open=<lead>&tab=overview|tasks|notes|activity`, Esc/X with
      unsaved-changes confirm): Overview (edit client/business/email/phone, stage, status; next task, latest note),
      Tasks (type, description, due date/time + timezone, reminder, complete/reopen/edit/delete, filters, sort,
      editable task types), Notes (revisions kept, archive/restore; lead-page notes shown read-only), Activity
- [x] Reminders: `services/reminders.py` in every engine pass / Celery beat `outreach.reminders` (60 s). Row-locked
      claims + unique (user, task, kind, fire_at) = once only. Reminder at its time; overdue notice 15 min after due;
      a reminder missed while the scheduler was off folds into the overdue notice. Recipient: assignee → creator →
      active admins. Never a lead
- [x] Optional reminder email via a separate SMTP channel (not the campaign mailboxes), only to Settings →
      Notifications → notification email: `NOTIFY_SMTP_HOST`, `NOTIFY_SMTP_PORT` (587), `NOTIFY_SMTP_USERNAME`,
      `NOTIFY_SMTP_PASSWORD`, `NOTIFY_SMTP_FROM`, `NOTIFY_SMTP_STARTTLS` (true). Off unless host + from are set.
      Claimed before sending, so a crash never sends twice
- [x] Notification bell (every page): unread badge (30 s poll), All/Unread/Overdue, read/unread, mark all read,
      dismiss, click → opportunity on its Tasks tab, opt-in desktop alerts
- [x] Verified: pytest 99 passed (12 new in tests/test_opportunities.py); ruff, `alembic check`, tsc, eslint,
      `next build`; headless-browser walkthrough (board, panel tabs, unsaved confirm, bell → Tasks tab, stage manager,
      light theme, phone) with no console errors or overflow
- [ ] Reminders only fire while the engine (or Celery beat + worker) runs. On a PC that sleeps or is off, reminders
      are delivered on the next pass after it starts (missed ones become overdue notices). For reliable timing, run
      the scheduler on an always-on server

## Prospects / Opportunities split + sidebar (2026-10-27)
- [x] Leads renamed Prospects in the UI and messages (`/leads/*` → 308 → `/prospects/*`, export `prospects-<date>.csv`,
      status `new_lead` shown as "New"); internal names (`lead_status`, `contacts` API) unchanged
- [x] Board shows only opportunities created on it (`crm_opportunities.on_board`, migration 10ed919f7ab4; existing
      rows start off the board, as chosen). "New opportunity" on the board reuses a prospect with the same email
      (fills blanks only, no duplicate); "Remove" takes one off the board and keeps everything
- [x] Opportunities board uses the full window width; sidebar grouped (Sales / Outreach / Insights & tools / Admin),
      larger rows, Opportunities second
- [x] Verified: pytest 101 passed; ruff, tsc, eslint, `next build`; browser check of sidebar, empty full-width board,
      New opportunity (prospect reuse), Prospects page, collapsed sidebar; no console errors

## Security review (2026-10-27)
- [x] Dependencies: fastapi 0.115.12→0.162.0 (starlette 0.46.2→1.10.0), python-multipart 0.0.20→0.0.32,
      cryptography 44.0.2→50.0.2, dnspython 2.7.0→2.9.0, pytest 8.3.5→9.1.1 (dev). pip-audit of the installed venv
      and `npm audit` (incl. dev): no known vulnerabilities
- [x] Fixes: constant-time CSRF comparison; /api/docs + /api/openapi.json off in production; current-password
      attempts rate limited per user; length cap on current_password; user PATCH ignores explicit nulls and validates
      the id; .xlsx expanded size capped at 200 MB (zip bomb); inbox email frame CSP blocks remote images/forms until
      "Show images"; frontend CSP (frame-ancestors/object-src/base-uri/form-action) + COOP
- [x] Verified: pytest 87 passed (5 new security tests), ruff, tsc, eslint, `next build` clean
- [ ] Open (decide when deploying): the API rate limit is keyed by session-cookie prefix, so unauthenticated
      clients can rotate cookies; behind the Next proxy every client shares one IP for the login limit unless
      TRUST_PROXY_HEADERS is set with a real reverse proxy. Embedded dev Postgres uses trust auth on 127.0.0.1
      (any local process can connect) — fine for a single-user dev PC, use the Docker/managed Postgres with a
      password in production

## Vercel deployment (2026-10-27)
- [x] Frontend: Vercel project `trace-crm` (root `apps/web`, Git-connected) → https://trace-crm-blush.vercel.app.
      `API_URL=https://trace-crm-api.vercel.app` (production) so the `/api/*` and `/u/*` rewrites reach the API; cookies
      stay first-party on the web domain
- [x] API: Vercel project `trace-crm-api` (FastAPI preset, `apps/api/vercel.json`, deployed with `vercel deploy --prod`
      from `apps/api`) → https://trace-crm-api.vercel.app. `.vercelignore` keeps `.env`, `.venv`, tests and
      `pyproject.toml` out (without it Vercel installs from requirements.txt). Production env: APP_ENV=production,
      COOKIE_SECURE, TRUST_PROXY_HEADERS, fresh SECRET_KEY/TOKEN_ENCRYPTION_KEYS, CRON_SECRET, REAL_SENDING=test_only,
      Google client + ALLOWED_TEST_RECIPIENTS copied from local
- [x] Database: Neon (Vercel Marketplace, free, iad1) `trace-crm-db`; app uses the pooled DATABASE_URL (postgres:// URLs
      are normalized to psycopg; server-side prepared statements off for the pooler). Migrations run from a dev PC:
      `DATABASE_URL=<DATABASE_URL_UNPOOLED> python -m alembic upgrade head`. Admin created with `app.cli create-admin`
- [x] Engine: `GET /api/cron/engine` (Bearer CRON_SECRET; 404 when unset) runs one `engine.tick()`; a Postgres
      advisory lock skips overlapping passes. Vercel Cron calls it daily (Hobby limit)
- [x] Verified: pytest 106 passed (5 new in tests/test_cron.py), ruff clean; live: health 200 with DB, cron 401 without
      the secret and a full pass with it, /api/docs 404, login → /me → create/delete contact through the web domain,
      Secure + HttpOnly session cookie
- [ ] Per-minute engine on Hobby: external pinger (cron-job.org) GET https://trace-crm-api.vercel.app/api/cron/engine
      every minute with header `Authorization: Bearer <CRON_SECRET>`
- [ ] Google Cloud Console: add redirect URI https://trace-crm-blush.vercel.app/api/email-accounts/oauth/google/callback
- [ ] API project is deployed by CLI, not Git: connect the repo with Root Directory `apps/api` for push-to-deploy
- [ ] Limits on Vercel: request bodies max 4.5 MB (CSV/XLSX imports larger than that fail before the 10 MB app cap);
      rate limiter is per function instance unless REDIS_URL is set; Hobby is for non-commercial use per Vercel terms

## Campaign lists, follow-ups and bulk management (2026-10-28)
- [x] Imported Lists: every committed import is a named list (`import_batches`, `import_batch_members` incl. prospects
      that were already in the CRM; prospects are never duplicated). `/lists` page: contacts / valid / duplicates /
      linked campaigns, view (Prospects `?batch_id=`), add to campaign, rename, export (formula-safe), delete (list only)
- [x] Shared prospect filter (`services/audience.py`): state, city, industry, list, tag, prospect + email status,
      previous campaign participation, custom field; `GET /api/contacts/count`. Prospects page gained list/state/city/
      industry filters
- [x] New-campaign wizard: details (type, location, industry), then audience: imported list (one click), smart
      filters with live count and fixed vs dynamic audience, or upload (the import flow, saved as a list and added
      whole). Dynamic audiences are kept up to date by the engine pass / Celery `outreach.sync_audiences`; prospects
      removed by hand are never re-added
- [x] Sequence: named emails, Active / Draft / Paused per email (prospects wait at a draft or paused email; nothing is
      skipped), optional "not before" date, Recipients view per email (will receive / already received / excluded by
      reason) with "Send now to everyone waiting". New follow-ups start as drafts
- [x] Campaign page tabs Overview / Contacts / Email Sequence / Email History / Analytics / Settings; header Send email
      (confirmation with total, valid, duplicates, unsubscribed, suppressed, replied, missing merge data, final
      eligible), Add follow-up, Add contacts (list / filters / upload), Pause/Resume, Export
- [x] Contacts tab: phone, state, email status, last sent, last reply, follow-up status; search/filter; select all
      matching; remove (prospect kept), pause/resume/stop per prospect, copy/move to another campaign, tag, export.
      Paused prospects are stopped by replies/unsubscribes/bounces like active ones; resuming a campaign or a prospect
      shifts pending follow-ups by the pause length instead of releasing a backlog
- [x] Email History (per email: dates, recipients, sent, failed, bounced, replied, unsubscribed; recipient list) and
      per-prospect history; Analytics with stated denominators ("delivered" = sent minus bounces; opens not tracked)
- [x] Exports (prospects, lists, campaign contacts) require the member role; viewers can no longer export
- [x] Migration 4b9f5ac7be9c (additive; up/down/up + `alembic check` clean)
- [x] Verified: pytest 136 passed (10 in test_import_lists.py, 12 in test_campaign_workflow.py); ruff, tsc, eslint,
      `next build` clean
- [x] Browser check (Edge, production build, throwaway database, sandbox mailbox only): 300-row XLSX → list →
      Prospects → wizard → campaign with all 300 → sequence with 2 follow-ups → confirmation (300 eligible) → 300 sent →
      reply + unsubscribe excluded from follow-up #1 (298 waiting) → send-now went out once per prospect up to the
      mailbox daily limit (200, the rest wait for tomorrow) → remove keeps the prospect → pause/resume; no horizontal
      overflow at 1366px and 390px in light and dark; no console errors
- [ ] Past imports can't become lists (the old audit log only kept counts, not which prospects were in each file)

## Blockers / limitations
- Docker Desktop not installed yet (user action). Dev uses embedded Postgres (pgserver).
- No Microsoft / OpenAI credentials yet. Google OAuth client is set by the user in apps/api/.env (git-ignored).
- pgserver's Windows build ships without Postgres timezone data; `scripts/dev_db.py` and the test conftest copy it in
  from the `tzdata` package (regression: tests/test_dashboard.py). Not an issue with the official Postgres image.
- `scripts/dev_db.py` (2026-10-27): records the data directory next to DATABASE_URL (`DEV_DB_PGDATA` in apps/api/.env)
  and refuses to start or rewrite .env when DATABASE_URL belongs to another database (other checkout, Docker, a server
  that is not running) unless `--switch` is given; rejects unknown options; `--stop` now really stops the server
  (it used to start it and return). Background: it silently moved the API from the OneDrive checkout's database to a
  stale copy; recovered with backups in D:\Development\Backups\Trace-Studio-Outreach\2026-10-27-db-recovery.
