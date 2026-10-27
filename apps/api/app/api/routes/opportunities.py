"""Opportunities CRM: customizable stages, board, opportunity detail, tasks, notes and activity history."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, reader, writer
from app.core.db import get_db
from app.models import Contact, CrmActivity, CrmNote, CrmTask, PipelineStage, User
from app.models.base import utcnow
from app.services import app_settings, notes, tasks
from app.services import opportunities as svc

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])
notes_router = APIRouter(prefix="/api/notes", tags=["opportunities"])


class StageIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    color: str = Field(default="#8a8579", max_length=20)
    lead_status: str | None = None


class StagePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    color: str | None = Field(default=None, max_length=20)
    lead_status: str | None = None


class StageOrder(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class OpportunityPatch(BaseModel):
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=300)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=60)
    stage_id: uuid.UUID | None = None
    status: str | None = Field(default=None, pattern="^(open|won|lost)$")
    value_cents: int | None = Field(default=None, ge=0, le=10**12)
    meeting_at: datetime | None = None


class OpportunityCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    stage_id: uuid.UUID | None = None
    value_cents: int | None = Field(default=None, ge=0, le=10**12)


class MoveIn(BaseModel):
    stage_id: uuid.UUID


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    task_type: str | None = Field(default=None, max_length=40)
    due_date: str | None = Field(default=None, max_length=10)
    due_time: str | None = Field(default=None, max_length=5)
    timezone: str | None = Field(default=None, max_length=64)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)


class TaskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=10000)
    task_type: str | None = Field(default=None, max_length=40)
    due_date: str | None = Field(default=None, max_length=10)
    due_time: str | None = Field(default=None, max_length=5)
    timezone: str | None = Field(default=None, max_length=64)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    completed: bool | None = None


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class TaskTypesIn(BaseModel):
    task_types: list[str] = Field(min_length=1, max_length=30)


def _contact(db: Session, contact_id: uuid.UUID) -> Contact:
    c = db.get(Contact, contact_id)
    if c is None or c.deleted_at:
        raise HTTPException(404, "Opportunity not found")
    return c


def _stage(db: Session, stage_id: uuid.UUID) -> PipelineStage:
    s = db.get(PipelineStage, stage_id)
    if s is None:
        raise HTTPException(404, "Stage not found")
    return s


def _task(db: Session, task_id: uuid.UUID) -> CrmTask:
    t = db.get(CrmTask, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    return t


def _note(db: Session, note_id: uuid.UUID) -> CrmNote:
    n = db.get(CrmNote, note_id)
    if n is None:
        raise HTTPException(404, "Note not found")
    return n


def _stages_payload(db: Session) -> list[dict]:
    counts = svc.counts_by_stage(db)
    return [{**svc.stage_out(s), "count": counts.get(s.id, 0)} for s in svc.stages(db)]


# --- stages ---------------------------------------------------------------------------------------

@router.get("/stages")
def list_stages(_: User = Depends(reader), db: Session = Depends(get_db)):
    return _stages_payload(db)


@router.post("/stages", status_code=201)
def create_stage(body: StageIn, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    try:
        s = svc.create_stage(db, body.name, body.color, body.lead_status)
    except svc.StageError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "stage.created", "pipeline_stage", s.id, request, name=s.name)
    db.commit()
    return _stages_payload(db)


@router.patch("/stages/{stage_id}")
def update_stage(stage_id: uuid.UUID, body: StagePatch, request: Request, user: User = Depends(admin),
                 db: Session = Depends(get_db)):
    s = _stage(db, stage_id)
    data = body.model_dump(exclude_unset=True)
    try:
        svc.update_stage(db, s, data)
    except svc.StageError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "stage.updated", "pipeline_stage", s.id, request, **{k: v for k, v in data.items() if v is not None})
    db.commit()
    return _stages_payload(db)


@router.put("/stages/order")
def reorder_stages(body: StageOrder, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    try:
        svc.reorder(db, body.ids)
    except svc.StageError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "stage.reordered", "pipeline_stage", None, request)
    db.commit()
    return _stages_payload(db)


@router.delete("/stages/{stage_id}")
def delete_stage(stage_id: uuid.UUID, request: Request, move_to: uuid.UUID | None = None, user: User = Depends(admin),
                 db: Session = Depends(get_db)):
    s = _stage(db, stage_id)
    target = _stage(db, move_to) if move_to else None
    name = s.name
    try:
        moved = svc.delete_stage(db, s, target, user)
    except svc.StageError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user, "stage.deleted", "pipeline_stage", stage_id, request, name=name, moved=moved,
          move_to=target.name if target else None)
    db.commit()
    return {"moved": moved, "stages": _stages_payload(db)}


# --- board and detail -----------------------------------------------------------------------------

@router.get("")
def board(q: str | None = None, _: User = Depends(reader), db: Session = Depends(get_db)):
    return {"stages": svc.board(db, q)}


@router.post("", status_code=201)
def create_opportunity(body: OpportunityCreate, request: Request, user: User = Depends(writer),
                       db: Session = Depends(get_db)):
    stage = _stage(db, body.stage_id) if body.stage_id else None
    try:
        contact, reused = svc.create_opportunity(db, body.model_dump(), stage, user)
    except svc.StageError as exc:
        db.rollback()
        raise HTTPException(409 if "already" in str(exc) else 422, str(exc)) from exc
    audit(db, user, "opportunity.created", "contact", contact.id, request, reused_prospect=reused)
    db.commit()
    return {**svc.detail(db, contact), "reused_prospect": reused}


@router.post("/{contact_id}/remove")
def remove_opportunity(contact_id: uuid.UUID, request: Request, user: User = Depends(writer),
                       db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    try:
        svc.remove_from_board(db, c, user)
    except svc.StageError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user, "opportunity.removed", "contact", c.id, request)
    db.commit()
    return {"message": "Removed from Opportunities"}


@router.get("/task-types")
def get_task_types(_: User = Depends(reader), db: Session = Depends(get_db)):
    return {"task_types": tasks.task_types(db), "default_timezone": tasks.default_timezone(db)}


@router.put("/task-types")
def put_task_types(body: TaskTypesIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    try:
        value = app_settings.update_section(db, "crm", {"task_types": body.task_types})
    except app_settings.SettingsError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "settings.updated", "settings", "crm", request, fields=["task_types"])
    db.commit()
    return {"task_types": value["task_types"], "default_timezone": tasks.default_timezone(db)}


@router.get("/{contact_id}")
def get_opportunity(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return svc.detail(db, _contact(db, contact_id))


@router.patch("/{contact_id}")
def update_opportunity(contact_id: uuid.UUID, body: OpportunityPatch, request: Request, user: User = Depends(writer),
                       db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    data = body.model_dump(exclude_unset=True)
    try:
        changed = svc.update_details(db, c, data, user)
        if data.get("stage_id"):
            svc.move(db, c, _stage(db, data["stage_id"]), user)
        if data.get("status"):
            svc.set_status(db, c, data["status"], user)
        if "value_cents" in data or "meeting_at" in data:
            opp = svc.get_or_create_opportunity(db, c, user)
            for f in ("value_cents", "meeting_at"):
                if f in data:
                    setattr(opp, f, data[f])
    except svc.StageError as exc:
        db.rollback()
        raise HTTPException(409 if "already uses" in str(exc) else 422, str(exc)) from exc
    c.updated_at = utcnow()
    audit(db, user, "opportunity.updated", "contact", c.id, request, fields=sorted(data), contact_fields=changed)
    db.commit()
    return svc.detail(db, c)


@router.post("/{contact_id}/move")
def move(contact_id: uuid.UUID, body: MoveIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    svc.move(db, c, _stage(db, body.stage_id), user)
    c.updated_at = utcnow()
    db.commit()
    return svc.detail(db, c)


# --- tasks ----------------------------------------------------------------------------------------

@router.get("/{contact_id}/tasks")
def list_tasks(contact_id: uuid.UUID, status: str = Query("all", pattern="^(all|open|upcoming|overdue|completed)$"),
               task_type: str | None = None, sort: str = Query("due", pattern="^(due|created|type)$"),
               _: User = Depends(reader), db: Session = Depends(get_db)):
    _contact(db, contact_id)
    stmt = select(CrmTask).where(CrmTask.contact_id == contact_id)
    if task_type:
        stmt = stmt.where(CrmTask.task_type == task_type)
    rows = [tasks.task_out(t) for t in db.scalars(stmt)]
    if status != "all":
        want = {"open": {"open", "overdue"}, "upcoming": {"open"}, "overdue": {"overdue"}, "completed": {"completed"}}[status]
        rows = [r for r in rows if r["status"] in want]
    key = {"due": lambda r: (r["status"] == "completed", r["due_at"] is None, r["due_at"] or ""),
           "created": lambda r: r["created_at"] or "", "type": lambda r: (r["task_type"], r["due_at"] or "")}[sort]
    return sorted(rows, key=key, reverse=(sort == "created"))


@router.post("/{contact_id}/tasks", status_code=201)
def create_task(contact_id: uuid.UUID, body: TaskIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    try:
        t = tasks.create(db, c, body.model_dump(exclude_unset=True), user)
    except tasks.TaskError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return tasks.task_out(t)


@router.patch("/tasks/{task_id}")
def update_task(task_id: uuid.UUID, body: TaskPatch, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = _task(db, task_id)
    data = body.model_dump(exclude_unset=True)
    done = data.pop("completed", None)
    try:
        if data:
            tasks.update(db, t, data, user)
        if done is not None:
            tasks.set_completed(db, t, done, user)
    except tasks.TaskError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return tasks.task_out(t)


@router.delete("/tasks/{task_id}")
def delete_task(task_id: uuid.UUID, user: User = Depends(writer), db: Session = Depends(get_db)):
    tasks.delete(db, _task(db, task_id), user)
    db.commit()
    return {"message": "Task deleted"}


# --- notes ----------------------------------------------------------------------------------------

@router.get("/{contact_id}/notes")
def list_notes(contact_id: uuid.UUID, include_archived: bool = False, _: User = Depends(reader),
               db: Session = Depends(get_db)):
    _contact(db, contact_id)
    return notes.listing(db, contact_id, include_archived)


@router.post("/{contact_id}/notes", status_code=201)
def add_note(contact_id: uuid.UUID, body: NoteIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    _contact(db, contact_id)
    try:
        n = notes.create(db, contact_id, body.body, user)
    except notes.NoteError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return notes.note_out(n, {user.id: user.full_name or user.email})


@notes_router.patch("/{note_id}")
def edit_note(note_id: uuid.UUID, body: NoteIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    n = _note(db, note_id)
    try:
        notes.edit(db, n, body.body, user)
    except notes.NoteError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return [x for x in notes.listing(db, n.contact_id, True) if x["id"] == str(n.id)][0]


@notes_router.post("/{note_id}/archive")
def archive_note(note_id: uuid.UUID, archived: bool = True, user: User = Depends(writer), db: Session = Depends(get_db)):
    n = _note(db, note_id)
    notes.set_archived(db, n, archived, user)
    db.commit()
    return [x for x in notes.listing(db, n.contact_id, True) if x["id"] == str(n.id)][0]


@notes_router.get("/{note_id}/revisions")
def note_revisions(note_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return notes.revisions(db, _note(db, note_id))


# --- activity -------------------------------------------------------------------------------------

@router.get("/{contact_id}/activity")
def activity(contact_id: uuid.UUID, limit: int = Query(200, ge=1, le=500), _: User = Depends(reader),
             db: Session = Depends(get_db)):
    c = _contact(db, contact_id)
    rows = list(db.scalars(select(CrmActivity).where(CrmActivity.contact_id == c.id)
                           .order_by(CrmActivity.created_at.desc()).limit(limit)))
    users = {u.id: (u.full_name or u.email) for u in db.scalars(
        select(User).where(User.id.in_({r.user_id for r in rows if r.user_id})))} if rows else {}
    out = [{"id": str(r.id), "type": r.type, "summary": r.summary, "at": r.created_at.isoformat(),
            "actor": users.get(r.user_id) or ("System" if r.user_id is None else "Former user"),
            "data": {k: v for k, v in (r.data or {}).items() if k in ("from_stage", "to_stage", "fields", "task_id", "note_id")}}
           for r in rows]
    if not any(r.type == "created" for r in rows):
        out.append({"id": "created", "type": "created", "summary": "Prospect created", "at": c.created_at.isoformat(),
                    "actor": "System", "data": {}})
    return sorted(out, key=lambda r: r["at"], reverse=True)
