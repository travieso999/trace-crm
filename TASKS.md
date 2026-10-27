# TASKS — running implementation checklist

Legend: [x] done & verified · [~] implemented, awaiting external verification · [ ] not started

## Phase A — Architecture
- [x] Workspace inspected (empty; Python/Git installed via winget; Docker pending — user installing)
- [x] docs/ARCHITECTURE.md, docs/DATABASE.md, docs/IMPLEMENTATION_PLAN.md, TASKS.md

## Phase B — Backend core
- [ ] Config, logging, security primitives
- [ ] Models + initial Alembic migration
- [ ] Auth (login/logout/me, sessions, CSRF, RBAC, rate limit)
- [ ] Contacts CRUD, search/filter, bulk, CSV import/export
- [ ] Companies CRUD
- [ ] Dashboard API

## Blockers / limitations
- Docker Desktop not installed yet (user action). Dev uses embedded Postgres (pgserver).
- No Gmail / Microsoft / OpenAI credentials yet.
