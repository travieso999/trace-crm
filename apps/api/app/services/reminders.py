"""Task reminders and overdue notices → in-app notifications (+ an optional email to the notification address).

Run by the engine every pass. Tasks are claimed with FOR UPDATE SKIP LOCKED and stamped in the same transaction;
notifications are unique per (user, task, kind, fire_at). Together that makes each reminder fire exactly once even
with several engines or a crash mid-pass. Reminder email never touches campaign mailboxes or leads.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.logging import log
from app.models import Contact, CrmTask, Notification, User
from app.models.base import utcnow
from app.services import app_settings, notify_email

logger = logging.getLogger("app.reminders")

BATCH = 200
EMAIL_STALE = timedelta(minutes=10)
# an open task becomes "overdue" (and gets its notice) this long after its due time
OVERDUE_GRACE = timedelta(minutes=15)


def recipients(db: Session, t: CrmTask) -> list[User]:
    """Who hears about a task: its assignee, else its creator, else every active admin. Never a lead."""
    for uid in (t.assignee_id, t.created_by_id):
        u = db.get(User, uid) if uid else None
        if u is not None and u.is_active:
            return [u]
    return list(db.scalars(select(User).where(User.role == "admin", User.is_active).order_by(User.created_at)))


def _who(c: Contact | None) -> str:
    if c is None:
        return "a deleted prospect"
    name = " ".join(p for p in (c.first_name, c.last_name) if p)
    return " · ".join(p for p in (name or c.email, c.company_name) if p)


def _when(t: CrmTask) -> str:
    from zoneinfo import ZoneInfo

    if not t.due_at:
        return ""
    local = t.due_at.astimezone(ZoneInfo(t.timezone)) if t.timezone else t.due_at
    return local.strftime("%a %b %d, %Y %I:%M %p ") + (t.timezone or "UTC")


def _notify(db: Session, t: CrmTask, kind: str, fire_at: datetime, email: bool) -> int:
    contact = db.get(Contact, t.contact_id)
    title = (f"Overdue: {t.title}" if kind == "task_overdue" else f"Reminder: {t.title}")[:300]
    body = f"{t.task_type} · {_who(contact)} · due {_when(t)}"
    created = 0
    for i, u in enumerate(recipients(db, t)):
        stmt = insert(Notification).values(
            user_id=u.id, kind=kind, title=title, body=body, contact_id=t.contact_id, task_id=t.id, fire_at=fire_at,
            data={"task_type": t.task_type, "due_at": t.due_at.isoformat() if t.due_at else None,
                  "timezone": t.timezone, "contact_name": _who(contact)},
            created_at=utcnow(), email_status="pending" if email and i == 0 else "none",
        ).on_conflict_do_nothing(constraint="uq_notifications_once").returning(Notification.id)
        created += len(db.execute(stmt).all())  # rowcount is unreliable for ON CONFLICT DO NOTHING
    return created


def _email_enabled(db: Session) -> bool:
    from app.core.config import get_settings

    s = app_settings.get_section(db, "notifications")
    return bool(get_settings().notify_email_configured and s.get("notification_email") and s.get("email_task_reminders", True))


def fire_due(now: datetime | None = None) -> dict:
    """Create the notifications that are due. Returns counts."""
    now = now or utcnow()
    made = {"reminders": 0, "overdue": 0}
    with SessionLocal() as db:
        email = _email_enabled(db)
        due = list(db.scalars(select(CrmTask).where(
            CrmTask.completed_at.is_(None), CrmTask.reminder_sent_at.is_(None), CrmTask.reminder_at <= now)
            .order_by(CrmTask.reminder_at).limit(BATCH).with_for_update(skip_locked=True)))
        for t in due:
            # missed while the scheduler was off and the task is already overdue: the overdue notice covers it
            if not (t.due_at and t.due_at <= now - OVERDUE_GRACE):
                made["reminders"] += _notify(db, t, "task_reminder", t.reminder_at, email)
            t.reminder_sent_at = now
        overdue = list(db.scalars(select(CrmTask).where(
            CrmTask.completed_at.is_(None), CrmTask.overdue_notified_at.is_(None), CrmTask.due_at <= now - OVERDUE_GRACE)
            .order_by(CrmTask.due_at).limit(BATCH).with_for_update(skip_locked=True)))
        for t in overdue:
            made["overdue"] += _notify(db, t, "task_overdue", t.due_at, email)
            t.overdue_notified_at = now
        db.commit()
    return made


def deliver_emails(now: datetime | None = None) -> dict:
    """Send pending reminder emails to the notification address. A row is claimed ("sending") and committed before
    the SMTP call, so a crash can never send it twice; claims stuck longer than EMAIL_STALE are marked failed."""
    now = now or utcnow()
    result = {"sent": 0, "failed": 0}
    with SessionLocal() as db:
        db.execute(Notification.__table__.update().where(
            Notification.email_status == "sending", Notification.created_at < now - EMAIL_STALE)
            .values(email_status="failed", email_error="interrupted while sending; not retried to avoid a duplicate"))
        to = app_settings.get_section(db, "notifications").get("notification_email")
        rows = list(db.scalars(select(Notification).where(Notification.email_status == "pending")
                               .order_by(Notification.created_at).limit(50).with_for_update(skip_locked=True)))
        if not rows:
            db.commit()
            return result
        if not (to and _email_enabled(db)):
            for n in rows:
                n.email_status, n.email_error = "none", "reminder email is not configured"
            db.commit()
            return result
        for n in rows:
            n.email_status = "sending"
        db.commit()
        for n in rows:
            try:
                notify_email.send(to, n.title, _email_text(n))
                n.email_status, n.email_sent_at = "sent", utcnow()
                result["sent"] += 1
            except notify_email.NotifyEmailError as exc:
                n.email_status, n.email_error = "failed", str(exc)[:1000]
                result["failed"] += 1
            db.commit()
    if result["sent"] or result["failed"]:
        log(logger, logging.INFO, "reminder_emails", **result)
    return result


def _email_text(n: Notification) -> str:
    from app.core.config import get_settings

    link = f"{get_settings().public_url.rstrip('/')}/opportunities?open={n.contact_id}&tab=tasks" if n.contact_id else ""
    return f"{n.title}\n\n{n.body or ''}\n\n{('Open: ' + link) if link else ''}\n\n— Trace CRM task reminder"


def tick(now: datetime | None = None) -> dict:
    made = fire_due(now)
    sent = deliver_emails(now)
    return {k: v for k, v in {**made, "emails": sent["sent"]}.items() if v}


def unread_filter(user_id):
    return (Notification.user_id == user_id, Notification.read_at.is_(None), Notification.dismissed_at.is_(None))

