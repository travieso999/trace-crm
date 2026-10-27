# Implementation Plan

Phases follow the master build prompt. Every phase ends with tests/builds run and `TASKS.md` updated.

| Phase | Scope | Exit criteria |
|---|---|---|
| A | Architecture, data model, docs | ARCHITECTURE / DATABASE / PLAN / TASKS written |
| B | FastAPI core, config, models, Alembic, auth, contacts, companies, CSV import, dashboard API | `pytest` green against real Postgres; migration upgrade/downgrade clean |
| C | Next.js shell, auth pages, dashboard, leads, companies (vertical slice), then all other pages | `next build` + `tsc --noEmit` + lint clean; pages hit real endpoints |
| D | Provider adapters (Gmail, Graph, Sandbox), OAuth flows, sending engine, reply sync, bounces, suppression | Mocked-provider tests for send, retry, reconcile, sync, bounce |
| E | Sequences, conditional follow-ups, cancellation on reply, business-day scheduling, mailbox rotation | Race/concurrency tests pass |
| F | AI personalization, website audits (SSRF-safe), CRM pipeline, reply classification | Unit tests with mocked AI + HTTP |
| G | Full QA pass: unit, integration, build, typecheck, lint, migration test, security review, E2E | All green or documented |
| H | Dockerfiles, Compose (dev + prod), Caddy HTTPS, backups, deployment + troubleshooting docs | `docker compose config` validates |

## Environment constraints (recorded 2026-10-26)

* Dev machine is Windows 10 without Docker. Postgres for dev/tests runs via the `pgserver` pip
  package (embedded PostgreSQL 16.2). Celery uses the `sqla+postgresql` broker locally when
  `REDIS_URL` is not set. Docker Compose is the path for staging/production.
* No Gmail / Microsoft / OpenAI credentials are available. Those integrations are tested against
  mocked HTTP and are labelled **Implemented — Awaiting External Verification**.
