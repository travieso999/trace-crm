"""Opportunity tasks: due date/time in the task's own timezone, reminders, lifecycle and history."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models import Contact, CrmTask, User
from app.models.base import utcnow
from app.services import app_settings
from app.services.contacts import log_activity

DEFAULT_TIME = time(9, 0)
MAX_REMINDER_MINUTES = 7 * 24 * 60


class TaskError(ValueError):
    pass


def task_types(db: Session) -> list[str]:
    return list(app_settings.get_section(db, "crm").get("task_types") or ["Other"])


def default_timezone(db: Session) -> str:
    return app_settings.get_section(db, "defaults").get("timezone") or "America/New_York"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TaskError(f"Unknown timezone {name!r}") from exc


def due_instant(due_date: str | None, due_time: str | None, tz: str) -> datetime | None:
    """The UTC instant for a date (YYYY-MM-DD) and optional time (HH:MM, default 09:00) in ``tz``."""
    if not due_date:
        if due_time:
            raise TaskError("Choose a due date for the time")
        return None
    try:
        d = date.fromisoformat(due_date)
        t = time.fromisoformat(due_time) if due_time else DEFAULT_TIME
    except ValueError as exc:
        raise TaskError("Due date must be YYYY-MM-DD and time HH:MM") from exc
    return datetime.combine(d, t, tzinfo=_zone(tz)).astimezone(ZoneInfo("UTC"))


def _reschedule(t: CrmTask) -> None:
    """Derive reminder_at; a changed due time or reminder fires again (and a new overdue notice is allowed)."""
    new = t.due_at - timedelta(minutes=t.reminder_minutes) if t.due_at and t.reminder_minutes is not None else None
    if new != t.reminder_at:
        t.reminder_at, t.reminder_sent_at = new, None
    if t.due_at is None or t.due_at > utcnow():
        t.overdue_notified_at = None


def apply(db: Session, t: CrmTask, data: dict) -> None:
    """Apply validated input (title, description, task_type, due_date, due_time, timezone, reminder_minutes)."""
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise TaskError("A task needs a title")
        t.title = title[:300]
    if "description" in data:
        t.description = (data["description"] or "").strip() or None
    if "task_type" in data:
        if data["task_type"] not in task_types(db):
            raise TaskError("Unknown task type")
        t.task_type = data["task_type"]
    if "timezone" in data and data["timezone"]:
        _zone(data["timezone"])
        t.timezone = data["timezone"]
    t.timezone = t.timezone or default_timezone(db)
    if "due_date" in data or "due_time" in data:
        t.due_at = due_instant(data.get("due_date"), data.get("due_time"), t.timezone)
    elif "due_at" in data:  # an exact instant (the lead page's task form)
        t.due_at = data["due_at"]
    if "reminder_minutes" in data:
        m = data["reminder_minutes"]
        if m is not None and not 0 <= int(m) <= MAX_REMINDER_MINUTES:
            raise TaskError("Reminder must be between the due time and 7 days before")
        t.reminder_minutes = None if m is None else int(m)
    if t.reminder_minutes is not None and t.due_at is None:
        raise TaskError("A reminder needs a due date")
    _reschedule(t)


def create(db: Session, contact: Contact, data: dict, user: User | None) -> CrmTask:
    t = CrmTask(contact_id=contact.id, title="", created_by_id=user.id if user else None,
                assignee_id=user.id if user else None, task_type=data.get("task_type") or "Follow-up")
    apply(db, t, {"task_type": t.task_type, **data})
    db.add(t)
    db.flush()
    log_activity(db, contact.id, "task_created", f"Task created: {t.title}", user, task_id=str(t.id),
                 task_type=t.task_type, due_at=t.due_at.isoformat() if t.due_at else None)
    return t


def update(db: Session, t: CrmTask, data: dict, user: User | None) -> CrmTask:
    before = (t.title, t.task_type, t.due_at, t.reminder_minutes, t.description)
    apply(db, t, data)
    if (t.title, t.task_type, t.due_at, t.reminder_minutes, t.description) != before:
        log_activity(db, t.contact_id, "task_updated", f"Task updated: {t.title}", user, task_id=str(t.id),
                     due_at=t.due_at.isoformat() if t.due_at else None)
    db.flush()
    return t


def set_completed(db: Session, t: CrmTask, done: bool, user: User | None) -> CrmTask:
    if done and t.completed_at is None:
        t.completed_at = utcnow()
        log_activity(db, t.contact_id, "task_completed", f"Task completed: {t.title}", user, task_id=str(t.id))
    elif not done and t.completed_at is not None:
        t.completed_at = None
        _reschedule(t)
        log_activity(db, t.contact_id, "task_reopened", f"Task reopened: {t.title}", user, task_id=str(t.id))
    db.flush()
    return t


def delete(db: Session, t: CrmTask, user: User | None) -> None:
    log_activity(db, t.contact_id, "task_deleted", f"Task deleted: {t.title}", user, task_id=str(t.id),
                 task_type=t.task_type)
    db.delete(t)
    db.flush()


def status_of(t: CrmTask, now: datetime | None = None) -> str:
    if t.completed_at:
        return "completed"
    if t.due_at and t.due_at < (now or utcnow()):
        return "overdue"
    return "open"


def task_out(t: CrmTask) -> dict:
    local = t.due_at.astimezone(_zone(t.timezone)) if t.due_at and t.timezone else t.due_at
    return {
        "id": str(t.id), "contact_id": str(t.contact_id), "title": t.title, "description": t.description,
        "task_type": t.task_type, "timezone": t.timezone, "status": status_of(t),
        "due_at": t.due_at.isoformat() if t.due_at else None,
        "due_date": local.date().isoformat() if local else None,
        "due_time": local.strftime("%H:%M") if local else None,
        "reminder_minutes": t.reminder_minutes,
        "reminder_at": t.reminder_at.isoformat() if t.reminder_at else None,
        "reminder_sent_at": t.reminder_sent_at.isoformat() if t.reminder_sent_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
