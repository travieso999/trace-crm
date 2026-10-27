"""The signed-in user's notification center (task reminders and overdue notices)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import current_user
from app.core.db import get_db
from app.models import CrmTask, Notification, User
from app.models.base import utcnow
from app.services.tasks import status_of

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class ReadIn(BaseModel):
    read: bool = True


def _mine(db: Session, user: User, nid: uuid.UUID) -> Notification:
    n = db.get(Notification, nid)
    if n is None or n.user_id != user.id or n.dismissed_at is not None:
        raise HTTPException(404, "Notification not found")
    return n


def _unread(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(
        Notification.user_id == user.id, Notification.read_at.is_(None), Notification.dismissed_at.is_(None))) or 0


def _out(n: Notification, task: CrmTask | None) -> dict:
    return {
        "id": str(n.id), "kind": n.kind, "title": n.title, "body": n.body,
        "contact_id": str(n.contact_id) if n.contact_id else None, "task_id": str(n.task_id) if n.task_id else None,
        "task_type": (n.data or {}).get("task_type"), "contact_name": (n.data or {}).get("contact_name"),
        "due_at": (n.data or {}).get("due_at"), "timezone": (n.data or {}).get("timezone"),
        "task_status": status_of(task) if task else "deleted",
        "created_at": n.created_at.isoformat(), "fire_at": n.fire_at.isoformat(),
        "read": n.read_at is not None,
    }


@router.get("")
def list_notifications(filter: str = Query("all", pattern="^(all|unread|overdue)$"),  # noqa: A002
                       limit: int = Query(50, ge=1, le=200), user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    stmt = select(Notification).where(Notification.user_id == user.id, Notification.dismissed_at.is_(None))
    if filter == "unread":
        stmt = stmt.where(Notification.read_at.is_(None))
    rows = list(db.scalars(stmt.order_by(Notification.created_at.desc()).limit(limit if filter != "overdue" else 200)))
    tasks = {t.id: t for t in db.scalars(select(CrmTask).where(CrmTask.id.in_({n.task_id for n in rows if n.task_id})))} \
        if rows else {}
    items = [_out(n, tasks.get(n.task_id)) for n in rows]
    if filter == "overdue":  # tasks that are overdue right now, whatever the notice was
        items = [i for i in items if i["task_status"] == "overdue"][:limit]
    return {"items": items, "unread": _unread(db, user)}


@router.get("/unread-count")
def unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)):
    latest = db.scalar(select(func.max(Notification.created_at)).where(
        Notification.user_id == user.id, Notification.dismissed_at.is_(None)))
    return {"unread": _unread(db, user), "latest_at": latest.isoformat() if latest else None}


@router.post("/{notification_id}/read")
def mark_read(notification_id: uuid.UUID, body: ReadIn, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    n = _mine(db, user, notification_id)
    n.read_at = utcnow() if body.read else None
    db.commit()
    return {"id": str(n.id), "read": n.read_at is not None, "unread": _unread(db, user)}


@router.post("/read-all")
def mark_all_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
               .values(read_at=utcnow()))
    db.commit()
    return {"unread": 0}


@router.post("/{notification_id}/dismiss")
def dismiss(notification_id: uuid.UUID, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = _mine(db, user, notification_id)
    n.dismissed_at = utcnow()
    n.read_at = n.read_at or utcnow()
    db.commit()
    return {"id": str(n.id), "unread": _unread(db, user)}
