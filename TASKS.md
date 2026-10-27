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
- [x] Verified 2026-10-26: pytest 19 passed (real Postgres), alembic upgrade/downgrade/upgrade + `alembic check` clean, ruff clean

## Phase C — Frontend
- [~] Next.js scaffold: app shell, login page, API client, types, shadcn/ui components (in progress)
- [ ] Dashboard, leads, companies pages
- [ ] Remaining pages

## Blockers / limitations
- Docker Desktop not installed yet (user action). Dev uses embedded Postgres (pgserver).
- No Gmail / Microsoft / OpenAI credentials yet.
