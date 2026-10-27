"""Engine pass over HTTP, for hosts without a long-running process (Vercel).

Called by Vercel Cron and by an external per-minute pinger with ``Authorization: Bearer <CRON_SECRET>``.
Returns 404 when CRON_SECRET is not set, so the endpoint does not exist on deployments that run the engine
as a process (``python -m app.cli engine``) or through Celery beat.
"""

import secrets

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.workers import engine

router = APIRouter(prefix="/api/cron", tags=["cron"], include_in_schema=False)

# Arbitrary constant: pg advisory lock id for "an engine pass is running"
ENGINE_LOCK_ID = 7_214_602_031


@router.get("/engine")
def engine_tick(request: Request):
    secret = get_settings().cron_secret
    if not secret:
        raise HTTPException(404, "Not found")
    if not secrets.compare_digest(request.headers.get("authorization", ""), f"Bearer {secret}"):
        raise HTTPException(401, "Unauthorized")
    # One pass at a time across all instances: overlapping pings skip instead of syncing a mailbox twice.
    # A transaction-scoped lock on its own connection also holds behind a transaction pooler.
    with get_engine().connect() as conn, conn.begin():
        if not conn.scalar(text("select pg_try_advisory_xact_lock(:id)"), {"id": ENGINE_LOCK_ID}):
            return {"skipped": "another engine pass is running"}
        return engine.tick()
