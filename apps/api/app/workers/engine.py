"""One pass of the background engine. Celery beat runs these steps on their own schedules in production;
``python -m app.cli engine`` runs them in a single loop for local development."""

import logging
from datetime import timedelta

from sqlalchemy import or_, select

from app.core.db import SessionLocal
from app.core.logging import log
from app.models import EmailAccount
from app.models.base import utcnow
from app.services import campaigns as campaign_service
from app.services import inbound, reminders, sending

logger = logging.getLogger("app.engine")

SYNC_EVERY = timedelta(minutes=2)


def activate_scheduled() -> int:
    with SessionLocal() as db:
        n = campaign_service.activate_scheduled(db)
        db.commit()
        return n


def sync_due(force: bool = False) -> int:
    cutoff = utcnow() - SYNC_EVERY
    with SessionLocal() as db:
        stmt = select(EmailAccount.id).where(EmailAccount.status.in_(("connected", "error")))
        if not force:
            stmt = stmt.where(or_(EmailAccount.last_synced_at.is_(None), EmailAccount.last_synced_at <= cutoff))
        ids = list(db.scalars(stmt))
    return sum(inbound.sync_mailbox(i).get("processed", 0) for i in ids)


def tick(force_sync: bool = False) -> dict:
    result = {
        "activated": activate_scheduled(),
        "replies": sync_due(force_sync),  # replies first, so they cancel follow-ups before we send
        "sends": sending.send_due(),
        "reconciled": sending.reconcile_unknown(),
        "reminders": reminders.tick(),
    }
    if result["activated"] or result["replies"] or result["sends"] or result["reminders"]:
        log(logger, logging.INFO, "engine_tick", **{k: v for k, v in result.items() if v})
    return result
