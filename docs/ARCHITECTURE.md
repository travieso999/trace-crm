# Trace Studio Outreach — Architecture

Private internal cold-outreach platform for Trace Studio. Target volume: 100–500 emails/day across a
handful of connected Gmail / Microsoft 365 mailboxes.

## 1. Topology

```
                 ┌──────────────── Caddy (HTTPS, :443) ────────────────┐
 browser ───────►│  /api/*, /u/*  → api:8000      everything else → web:3000 │
                 └──────────────────────────────────────────────────────┘
                          │                                   │
                 ┌────────▼────────┐                 ┌────────▼────────┐
                 │ FastAPI (api)   │                 │ Next.js (web)   │
                 │ REST + OpenAPI  │                 │ App Router UI   │
                 └───┬────────┬────┘                 └─────────────────┘
                     │        │ enqueue
               ┌─────▼──┐  ┌──▼────┐   ┌──────────────┐   ┌──────────────┐
               │Postgres│  │ Redis │◄──┤ celery worker│   │ celery beat  │
               └─────▲──┘  └───────┘   └──────┬───────┘   └──────┬───────┘
                     └────────────────────────┴── shared app code ┘
                                              │
                         Gmail API / Microsoft Graph / OpenAI / websites
```

* Postgres and Redis are only on the internal Docker network — never published.
* In local development the Next.js dev server proxies `/api/*` to FastAPI (rewrites), so the browser
  always talks same-origin and session cookies work without CORS.

## 2. Repository layout

```
apps/api/            FastAPI app, SQLAlchemy models, Alembic, Celery workers (one Python package)
  app/core/          config, security (argon2, sessions, CSRF), crypto (Fernet), logging
  app/models/        SQLAlchemy 2 ORM models
  app/schemas/       Pydantic request/response models
  app/api/routes/    REST routers (/api/auth, /api/contacts, ...)
  app/services/      domain logic (imports, campaigns state machine, rendering, scheduling, ...)
  app/providers/     mail provider adapters (gmail, microsoft, sandbox), AI adapters
  app/workers/       Celery app + tasks (dispatcher, sender, reconciler, sync, audits)
  alembic/           migrations
  tests/             pytest suite (real Postgres, mocked providers)
apps/web/            Next.js App Router frontend
infrastructure/      Caddyfile, Dockerfiles, compose overrides
scripts/             backup/restore, dev helpers
docs/                architecture, database, deployment, troubleshooting
```

**Deviation from the suggested structure:** Celery workers live in `apps/api/app/workers` instead of a
top-level `workers/` folder. Workers share every model, service, and provider adapter with the API; a
separate package would need either a duplicated codebase or a shared library with its own packaging.
The worker *containers* are still separate services built from the same image.

## 3. Key engineering decisions

| Decision | Rationale |
|---|---|
| **Synchronous SQLAlchemy** everywhere | Celery tasks are sync; one code path for API + workers. FastAPI runs sync endpoints in a threadpool, plenty for an internal tool. |
| **Server-side sessions** in Postgres, HttpOnly cookie, SHA-256 hashed token | Revocable, no JWTs in the browser. |
| **CSRF**: double-submit token stored on the session, sent as `X-CSRF-Token` | Required for every unsafe method on cookie-authenticated routes. |
| **Argon2id** (argon2-cffi defaults) | Spec requirement. |
| **Fernet (MultiFernet)** for OAuth tokens | Authenticated encryption, supports key rotation via comma-separated keys. |
| **Provider adapter** interface (`MailProvider`) | Gmail, Microsoft Graph, and a dev-only **Sandbox** provider that writes `.eml` files locally. |
| **Polling sync** for replies (Gmail `history.list`, Graph `delta`) | Push requires public endpoints + GCP Pub/Sub; polling every 2 min is supported, robust, and self-healing (cursor reset on expiry). |
| **Own RFC 5322 Message-ID** on every send | Enables threading (`In-Reply-To`/`References`), reply matching, and reconciliation after ambiguous timeouts (search provider by Message-ID). |
| **Row locks (`FOR UPDATE`) for send reservation** | Mailbox row lock serialises rate-limit accounting; campaign-contact row lock serialises with reply processing → no follow-up after a processed reply. |
| **Unique `(campaign_contact_id, sequence_step_id)`** on scheduled messages | Database-level duplicate-send prevention. |
| Celery broker: Redis (prod) / `sqla+postgresql` (dev fallback) | Lets the whole stack run on a dev machine without Redis/Docker. |

## 4. Sending pipeline

1. **Beat → `dispatch_due`** (every 60 s): selects active campaign-contacts with `next_send_at <= now`
   (`FOR UPDATE SKIP LOCKED`), inserts a `scheduled_messages` row (`ON CONFLICT DO NOTHING`), enqueues `send_message`.
2. **`send_message(id)`**, phase 1 (reservation transaction):
   lock scheduled message → lock campaign-contact → re-check campaign status, contact eligibility,
   global suppression, reply status, mailbox status/circuit breaker, sending window, mailbox + campaign
   daily limits, minimum spacing → render template (blocks on unresolved variables) → status `sending`, commit.
3. Phase 2: just-in-time thread check (follow-ups only) → provider `send`.
4. Phase 3 (result transaction): success → `sent_messages` row, email event, advance step & compute
   next `next_send_at` (business days, recipient time zone, window). Temporary error → exponential backoff,
   mailbox failure counter / circuit breaker. Permanent error → failed. Auth error → mailbox `auth_error`,
   message re-queued. **Ambiguous timeout** → status `unknown`, the reconciler later searches the
   provider for our Message-ID before deciding to mark sent or re-queue.

## 5. Reply sync

`sync_mailbox` (every 2 min per mailbox) pulls new inbound messages, fetches raw MIME, parses with the
stdlib `email` package, dedupes on `(email_account_id, provider_message_id)`, classifies (bounce DSN,
auto-reply, unsubscribe request, human reply), matches to a campaign-contact by provider thread id →
`In-Reply-To/References` → sender address, then under a row lock marks the campaign-contact replied and
cancels pending scheduled messages. Bounces suppress the address; unsubscribe replies suppress globally.

## 6. Security summary

See `docs/SECURITY.md` for the full list. Highlights: no credentials in logs (log filter), OAuth tokens
never serialised to API responses, OAuth `state` + PKCE stored server-side with 10-minute expiry, SSRF-safe
fetcher (DNS resolved and validated, connection pinned to the validated IP, re-validated on every redirect,
size/time caps), inbound HTML sanitised with `nh3` and rendered in a sandboxed iframe, strict security headers.
