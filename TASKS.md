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
- [ ] Remaining pages: campaigns, pipeline, email accounts, inbox, sequences, AI, audits, analytics, suppressions, settings
      (campaign assignment in leads bulk bar waits on the campaigns API)

## Blockers / limitations
- Docker Desktop not installed yet (user action). Dev uses embedded Postgres (pgserver).
- No Gmail / Microsoft / OpenAI credentials yet.
- pgserver's Windows build ships without Postgres timezone data; `scripts/dev_db.py` and the test conftest copy it in
  from the `tzdata` package (regression: tests/test_dashboard.py). Not an issue with the official Postgres image.
