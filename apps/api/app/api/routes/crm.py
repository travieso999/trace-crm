"""Pipeline board, opportunities (deal value / meeting) and follow-up tasks."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.db import get_db
from app.models import Contact, CrmOpportunity, CrmTask, EmailEvent, User
from app.models.crm import LEAD_STATUSES
from app.schemas.common import Message
from app.services import tasks as task_service
from app.services.contacts import log_activity, set_lead_status

router = APIRouter(prefix="/api", tags=["pipeline"])

PER_STAGE = 50


class OpportunityIn(BaseModel):
    value_cents: int | None = Field(default=None, ge=0, le=10**12)
    meeting_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=20000)


class TaskIn(BaseModel):
    contact_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    due_at: datetime | None = None


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    due_at: datetime | None = None
    completed: bool | None = None


def _opp_out(o: CrmOpportunity | None) -> dict | None:
    if o is None:
        return None
    return {"value_cents": o.value_cents, "meeting_at": o.meeting_at.isoformat() if o.meeting_at else None,
            "notes": o.notes, "closed_at": o.closed_at.isoformat() if o.closed_at else None}


def _task_out(t: CrmTask, contact: Contact | None = None) -> dict:
    out = {"id": str(t.id), "contact_id": str(t.contact_id), "title": t.title,
           "due_at": t.due_at.isoformat() if t.due_at else None,
           "completed_at": t.completed_at.isoformat() if t.completed_at else None, "created_at": t.created_at.isoformat()}
    if contact is not None:
        out["contact"] = {"email": contact.email, "first_name": contact.first_name, "last_name": contact.last_name,
                          "company_name": contact.company_name}
    return out


@router.get("/pipeline")
def pipeline(q: str | None = None, _: User = Depends(reader), db: Session = Depends(get_db)):
    base = select(Contact).where(Contact.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        base = base.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    sub = base.subquery()
    counts = dict(db.execute(select(sub.c.lead_status, func.count()).group_by(sub.c.lead_status)).all())
    values = dict(db.execute(
        select(sub.c.lead_status, func.coalesce(func.sum(CrmOpportunity.value_cents), 0))
        .join(CrmOpportunity, CrmOpportunity.contact_id == sub.c.id).group_by(sub.c.lead_status)).all())
    stages = []
    for stage in LEAD_STATUSES:
        contacts = list(db.scalars(base.where(Contact.lead_status == stage)
                                   .order_by(Contact.updated_at.desc(), Contact.id).limit(PER_STAGE)))
        ids = [c.id for c in contacts]
        opps = {o.contact_id: o for o in db.scalars(select(CrmOpportunity).where(CrmOpportunity.contact_id.in_(ids)))} \
            if ids else {}
        tasks = dict(db.execute(select(CrmTask.contact_id, func.count()).where(
            CrmTask.contact_id.in_(ids), CrmTask.completed_at.is_(None)).group_by(CrmTask.contact_id)).all()) if ids else {}
        stages.append({
            "stage": stage, "count": counts.get(stage, 0), "value_cents": int(values.get(stage, 0) or 0),
            "items": [{
                "id": str(c.id), "email": c.email, "first_name": c.first_name, "last_name": c.last_name,
                "company_name": c.company_name, "state": c.state,
                "last_replied_at": c.last_replied_at.isoformat() if c.last_replied_at else None,
                "last_contacted_at": c.last_contacted_at.isoformat() if c.last_contacted_at else None,
                "updated_at": c.updated_at.isoformat(), "opportunity": _opp_out(opps.get(c.id)),
                "open_tasks": tasks.get(c.id, 0),
            } for c in contacts],
        })
    return {"stages": stages}


def _contact(db: Session, contact_id: uuid.UUID) -> Contact:
    c = db.get(Contact, contact_id)
    if c is None or c.deleted_at:
        raise HTTPException(404, "Contact not found")
    return c


@router.get("/contacts/{contact_id}/opportunity")
def get_opportunity(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    _contact(db, contact_id)
    return _opp_out(db.scalar(select(CrmOpportunity).where(CrmOpportunity.contact_id == contact_id)))


@router.put("/contacts/{contact_id}/opportunity")
def put_opportunity(contact_id: uuid.UUID, body: OpportunityIn, request: Request, user: User = Depends(writer),
                    db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    o = db.scalar(select(CrmOpportunity).where(CrmOpportunity.contact_id == contact_id))
    if o is None:
        o = CrmOpportunity(contact_id=c.id, owner_id=user.id)
        db.add(o)
    data = body.model_dump(exclude_unset=True)
    new_meeting = "meeting_at" in data and data["meeting_at"] and data["meeting_at"] != o.meeting_at
    for f, v in data.items():
        setattr(o, f, v)
    if new_meeting:
        db.add(EmailEvent(type="meeting_booked", contact_id=c.id, data={"meeting_at": o.meeting_at.isoformat()}))
        log_activity(db, c.id, "meeting", f"Meeting set for {o.meeting_at:%b %d, %Y %H:%M} UTC", user)
        if c.lead_status in ("new_lead", "contacted", "replied", "interested"):
            set_lead_status(db, c, "meeting_scheduled", user, reason="meeting_booked")
    if "value_cents" in data:
        log_activity(db, c.id, "deal_value", f"Deal value set to ${(o.value_cents or 0) / 100:,.2f}", user)
    audit(db, user, "opportunity.updated", "contact", c.id, request, fields=sorted(data))
    db.commit()
    return _opp_out(o)


@router.get("/tasks")
def list_tasks(contact_id: uuid.UUID | None = None, open: bool = True, _: User = Depends(reader),  # noqa: A002
               db: Session = Depends(get_db)):
    stmt = select(CrmTask, Contact).join(Contact, Contact.id == CrmTask.contact_id).where(Contact.deleted_at.is_(None))
    if contact_id:
        stmt = stmt.where(CrmTask.contact_id == contact_id)
    if open:
        stmt = stmt.where(CrmTask.completed_at.is_(None))
    rows = db.execute(stmt.order_by(CrmTask.completed_at.desc().nulls_first(), CrmTask.due_at.asc().nulls_last())
                      .limit(500)).all()
    return [_task_out(t, c) for t, c in rows]


@router.post("/tasks", status_code=201)
def create_task(body: TaskIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _contact(db, body.contact_id)
    try:
        t = task_service.create(db, c, {"title": body.title, "due_at": body.due_at}, user)
    except task_service.TaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _task_out(t)


@router.patch("/tasks/{task_id}")
def update_task(task_id: uuid.UUID, body: TaskPatch, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = db.get(CrmTask, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    data = body.model_dump(exclude_unset=True)
    done = data.pop("completed", None)
    try:
        if data:
            task_service.update(db, t, data, user)
        if done is not None:
            task_service.set_completed(db, t, bool(done), user)
    except task_service.TaskError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return _task_out(t)


@router.delete("/tasks/{task_id}", response_model=Message)
def delete_task(task_id: uuid.UUID, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = db.get(CrmTask, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    task_service.delete(db, t, user)
    db.commit()
    return Message(message="Task deleted")
