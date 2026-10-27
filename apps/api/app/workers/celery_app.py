"""Celery app for production (Redis broker). Run with:

    celery -A app.workers.celery_app worker --beat --loglevel=INFO   (add --pool=solo on Windows)
"""

import uuid

from celery import Celery

from app.core.config import get_settings

celery = Celery("trace_outreach", broker=get_settings().broker_url)
celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    timezone="UTC",
    beat_schedule={
        "dispatch": {"task": "outreach.dispatch", "schedule": 60.0},
        "activate-scheduled": {"task": "outreach.activate_scheduled", "schedule": 60.0},
        "sync": {"task": "outreach.sync_due", "schedule": 120.0},
        "reconcile": {"task": "outreach.reconcile", "schedule": 300.0},
        "reminders": {"task": "outreach.reminders", "schedule": 60.0},
    },
)


@celery.task(name="outreach.dispatch")
def dispatch() -> int:
    from app.core.db import SessionLocal
    from app.services import sending

    with SessionLocal() as db:
        ids = sending.dispatch_due(db)
    for sm_id in ids:
        send_message.delay(str(sm_id))
    return len(ids)


@celery.task(name="outreach.send_message")
def send_message(sm_id: str) -> str:
    from app.services import sending

    return sending.send_scheduled(uuid.UUID(sm_id))


@celery.task(name="outreach.activate_scheduled")
def activate_scheduled() -> int:
    from app.workers import engine

    return engine.activate_scheduled()


@celery.task(name="outreach.sync_due")
def sync_due() -> int:
    from app.workers import engine

    return engine.sync_due()


@celery.task(name="outreach.reconcile")
def reconcile() -> dict:
    from app.services import sending

    return sending.reconcile_unknown()


@celery.task(name="outreach.reminders")
def task_reminders() -> dict:
    from app.services import reminders

    return reminders.tick()
