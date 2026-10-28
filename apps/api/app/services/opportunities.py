"""Opportunities board: customizable stages, creating/removing opportunities, moves and contact-detail edits.

The board shows only opportunities created on it (``crm_opportunities.on_board``); prospects imported for campaigns
are not opportunities until added. A card's column is the explicit ``stage_id`` when set, otherwise the stage linked
to the prospect's lead status, otherwise the first stage.
"""

import re
import uuid

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from app.models import Company, Contact, CrmNote, CrmOpportunity, CrmTask, PipelineStage, User
from app.models.base import utcnow
from app.models.crm import LEAD_STATUSES
from app.services import campaigns as campaign_service
from app.services.contacts import apply_fields, find_active_contact, log_activity, make_client
from app.services.normalize import InvalidEmail, normalize_email

PER_STAGE = 50
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
OPP_STATUSES = ("open", "won", "lost")
# The board's starting columns (same as the migration that created pipeline_stages): (lead status, name, color)
DEFAULT_STAGES = [
    ("new_lead", "New lead", "#8a8579"),
    ("contacted", "Contacted", "#5b8def"),
    ("replied", "Replied", "#a78bfa"),
    ("interested", "Interested", "#f5a524"),
    ("meeting_scheduled", "Meeting scheduled", "#22b8cf"),
    ("proposal_sent", "Proposal sent", "#ff5a33"),
    ("won", "Won", "#2fb37a"),
    ("lost", "Lost", "#6b6b6b"),
    ("not_interested", "Not interested", "#b8401f"),
]


class StageError(ValueError):
    pass


# --- stages ---------------------------------------------------------------------------------------

def stages(db: Session) -> list[PipelineStage]:
    return list(db.scalars(select(PipelineStage).order_by(PipelineStage.position, PipelineStage.created_at)))


def _validate(name: str | None, color: str | None, lead_status: str | None) -> None:
    if name is not None and not name.strip():
        raise StageError("A stage needs a name")
    if color is not None and not COLOR.match(color):
        raise StageError("Choose a color like #ff5a33")
    if lead_status is not None and lead_status not in LEAD_STATUSES:
        raise StageError("Unknown lead status")


def _check_link_free(db: Session, lead_status: str | None, stage_id: uuid.UUID | None = None) -> None:
    if lead_status:
        owner = db.scalar(select(PipelineStage).where(PipelineStage.lead_status == lead_status))
        if owner and owner.id != stage_id:
            raise StageError(f"“{owner.name}” is already linked to that lead status")


def create_stage(db: Session, name: str, color: str, lead_status: str | None = None) -> PipelineStage:
    _validate(name, color, lead_status)
    _check_link_free(db, lead_status)
    last = db.scalar(select(func.max(PipelineStage.position))) or 0
    s = PipelineStage(name=name.strip()[:80], color=color.lower(), position=last + 1, lead_status=lead_status)
    db.add(s)
    db.flush()
    return s


def update_stage(db: Session, stage: PipelineStage, data: dict) -> PipelineStage:
    _validate(data.get("name"), data.get("color"), data.get("lead_status"))
    if "lead_status" in data:
        _check_link_free(db, data["lead_status"], stage.id)
        stage.lead_status = data["lead_status"]
    if data.get("name") is not None:
        stage.name = data["name"].strip()[:80]
    if data.get("color") is not None:
        stage.color = data["color"].lower()
    db.flush()
    return stage


def reorder(db: Session, ids: list[uuid.UUID]) -> None:
    current = stages(db)
    if sorted(ids) != sorted(s.id for s in current):
        raise StageError("The new order must list every stage exactly once")
    by_id = {s.id: s for s in current}
    for i, sid in enumerate(ids):
        by_id[sid].position = i
    db.flush()


def _board(db: Session, q: str | None = None):
    """(stages, column expression, query builder) for the board. The column is the explicit opportunity stage,
    else the stage linked to the lead's status, else the first stage."""
    all_stages = stages(db)
    if not all_stages:
        raise StageError("No stages")
    linked = PipelineStage.__table__.alias("linked")
    column = func.coalesce(CrmOpportunity.stage_id, linked.c.id, literal(all_stages[0].id, UUID(as_uuid=True)))

    def query(*cols):
        stmt = (select(*cols).select_from(Contact)
                .join(CrmOpportunity, and_(CrmOpportunity.contact_id == Contact.id, CrmOpportunity.on_board.is_(True)))
                .outerjoin(linked, linked.c.lead_status == Contact.lead_status)
                .where(Contact.deleted_at.is_(None)))
        if q:
            like = f"%{q.strip().lower()}%"
            stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                                  func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like),
                                  func.lower(Contact.phone).like(like)))
        return stmt

    return all_stages, column, query


def counts_by_stage(db: Session) -> dict[uuid.UUID, int]:
    _, column, query = _board(db)
    return dict(db.execute(query(column, func.count()).group_by(column)).all())


def contacts_in_stage(db: Session, stage_id: uuid.UUID) -> list[Contact]:
    _, column, query = _board(db)
    return list(db.scalars(query(Contact).where(column == stage_id)))


def delete_stage(db: Session, stage: PipelineStage, move_to: PipelineStage | None, user: User | None) -> int:
    """Delete a stage, moving its opportunities to ``move_to`` first. Leads and opportunities are never deleted and
    lead statuses are left as they are; the stage's lead-status link moves to ``move_to`` when that has none."""
    others = [s for s in stages(db) if s.id != stage.id]
    if not others:
        raise StageError("The board needs at least one stage")
    members = contacts_in_stage(db, stage.id)
    if members and move_to is None:
        raise StageError("Choose a stage to move this stage's opportunities to")
    if move_to is not None and move_to.id == stage.id:
        raise StageError("Choose a different stage")
    for c in members:
        opp = get_or_create_opportunity(db, c, user)
        opp.stage_id = move_to.id
        log_activity(db, c.id, "stage_change", f"Moved to {move_to.name} (stage “{stage.name}” was deleted)", user,
                     from_stage=stage.name, to_stage=move_to.name)
    if stage.lead_status and move_to is not None and move_to.lead_status is None:
        status, stage.lead_status = stage.lead_status, None
        db.flush()  # release the unique link before giving it to the target
        move_to.lead_status = status
        db.flush()
    db.delete(stage)
    db.flush()
    for i, s in enumerate(stages(db)):
        s.position = i
    db.flush()
    return len(members)


# --- opportunities --------------------------------------------------------------------------------

def get_or_create_opportunity(db: Session, contact: Contact, user: User | None) -> CrmOpportunity:
    opp = db.scalar(select(CrmOpportunity).where(CrmOpportunity.contact_id == contact.id))
    if opp is None:
        opp = CrmOpportunity(contact_id=contact.id, owner_id=user.id if user else None)
        db.add(opp)
        db.flush()
    return opp


def create_opportunity(db: Session, data: dict, stage: PipelineStage | None, user: User | None) -> tuple[Contact, bool]:
    """Put a person on the board. An existing prospect with the same email is reused (blank fields are filled in,
    filled ones are never overwritten) instead of creating a duplicate. Returns (contact, reused)."""
    try:
        email = normalize_email(data.get("email") or "")
    except InvalidEmail as exc:
        raise StageError(str(exc)) from exc
    contact = find_active_contact(db, email)
    reused = contact is not None
    if contact is None:
        contact = Contact(email=email, source="opportunity")
        db.add(contact)
        apply_fields(db, contact, {k: data.get(k) for k in ("first_name", "last_name", "company_name", "phone")
                                   if data.get(k)}, link_company=True)
        db.flush()
        log_activity(db, contact.id, "created", "Created on the Opportunities board", user)
    else:
        blanks = {k: data[k] for k in ("first_name", "last_name", "company_name", "phone")
                  if data.get(k) and not getattr(contact, k)}
        if blanks:
            apply_fields(db, contact, blanks, link_company=True)
    opp = get_or_create_opportunity(db, contact, user)
    if opp.on_board:
        raise StageError("This person is already an opportunity")
    opp.on_board = True
    if data.get("value_cents") is not None:
        opp.value_cents = data["value_cents"]
    target = stage or stages(db)[0]
    move(db, contact, target, user)
    log_activity(db, contact.id, "opportunity_created", f"Added to Opportunities in {target.name}", user,
                 to_stage=target.name)
    db.flush()
    return contact, reused


def remove_from_board(db: Session, contact: Contact, user: User | None) -> None:
    """Take an opportunity off the board. The person, their tasks, notes and history are all kept."""
    opp = db.scalar(select(CrmOpportunity).where(CrmOpportunity.contact_id == contact.id))
    if opp is None or not opp.on_board:
        raise StageError("This person isn't on the Opportunities board")
    opp.on_board = False
    log_activity(db, contact.id, "opportunity_removed", "Removed from Opportunities (history kept)", user)
    db.flush()


def stage_of(db: Session, contact: Contact, opp: CrmOpportunity | None) -> PipelineStage:
    all_stages = stages(db)
    by_id = {s.id: s for s in all_stages}
    if opp and opp.stage_id in by_id:
        return by_id[opp.stage_id]
    return next((s for s in all_stages if s.lead_status == contact.lead_status), all_stages[0])


def _status_for(stage: PipelineStage) -> str | None:
    return {"won": "won", "lost": "lost", "not_interested": "lost"}.get(stage.lead_status or "", None)


def move(db: Session, contact: Contact, stage: PipelineStage, user: User | None) -> CrmOpportunity:
    opp = get_or_create_opportunity(db, contact, user)
    old = stage_of(db, contact, opp)
    opp.stage_id = stage.id
    status = _status_for(stage)
    if status:
        opp.status = status
        opp.closed_at = opp.closed_at or utcnow()
    elif _status_for(old) and opp.status != "open":
        opp.status, opp.closed_at = "open", None  # reopened by moving out of Won/Lost
    old_status = contact.lead_status
    if stage.lead_status and stage.lead_status != contact.lead_status:
        contact.lead_status = stage.lead_status
        if stage.lead_status == "not_interested":
            campaign_service.stop_contact_everywhere(db, contact.id, "not_interested")
    if contact.lead_status == "won" or opp.status == "won":
        make_client(db, contact)
    if old.id != stage.id:
        log_activity(db, contact.id, "stage_change", f"Stage: {old.name} → {stage.name}", user, from_stage=old.name,
                     to_stage=stage.name, old=old_status, new=contact.lead_status, reason="opportunity_board")
    db.flush()
    return opp


def board(db: Session, q: str | None) -> list[dict]:
    all_stages, column, query = _board(db, q)
    totals = {sid: (n, v) for sid, n, v in db.execute(
        query(column, func.count(), func.coalesce(func.sum(CrmOpportunity.value_cents), 0)).group_by(column))}
    out = []
    for s in all_stages:
        rows = list(db.execute(query(Contact, CrmOpportunity).where(column == s.id)
                               .order_by(Contact.updated_at.desc(), Contact.id).limit(PER_STAGE)))
        ids = [c.id for c, _ in rows]
        next_task: dict[uuid.UUID, CrmTask] = {}
        open_tasks: dict[uuid.UUID, int] = {}
        if ids:
            open_q = select(CrmTask).where(CrmTask.contact_id.in_(ids), CrmTask.completed_at.is_(None))
            for t in db.scalars(open_q.order_by(CrmTask.due_at.asc().nulls_last())):
                open_tasks[t.contact_id] = open_tasks.get(t.contact_id, 0) + 1
                if t.due_at and t.contact_id not in next_task:
                    next_task[t.contact_id] = t
        n, value = totals.get(s.id, (0, 0))
        out.append({
            **stage_out(s), "count": n, "value_cents": int(value or 0),
            "items": [card_out(c, o, s, next_task.get(c.id), open_tasks.get(c.id, 0)) for c, o in rows],
        })
    return out


# --- detail and edits -----------------------------------------------------------------------------

CONTACT_FIELDS = ("first_name", "last_name", "company_name", "email", "phone")
FIELD_LABELS = {"first_name": "first name", "last_name": "last name", "company_name": "business name",
                "email": "email", "phone": "phone"}


def update_details(db: Session, contact: Contact, data: dict, user: User | None) -> list[str]:
    """Edit the lead's client/business/email/phone. Email stays unique among leads; a changed business name links
    the lead to the matching company (creating it only if none exists) instead of renaming a shared company."""
    changes = {f: data[f] for f in CONTACT_FIELDS if f in data}
    if "email" in changes:
        try:
            changes["email"] = normalize_email(changes["email"] or "")
        except InvalidEmail as exc:
            raise StageError(str(exc)) from exc
        other = find_active_contact(db, changes["email"])
        if other and other.id != contact.id:
            raise StageError("Another prospect already uses this email")
    before = {f: getattr(contact, f) for f in changes}
    if "company_name" in changes and (changes["company_name"] or "").strip() != (contact.company_name or ""):
        contact.company_id = None  # re-link by name below
    apply_fields(db, contact, changes, link_company=True)
    changed = [f for f in changes if getattr(contact, f) != before[f]]
    if changed:
        parts = [f"{FIELD_LABELS[f]}: {before[f] or '—'} → {getattr(contact, f) or '—'}" for f in changed]
        log_activity(db, contact.id, "contact_updated", "Updated " + "; ".join(parts), user, fields=changed,
                     before={f: before[f] for f in changed}, after={f: getattr(contact, f) for f in changed})
    db.flush()
    return changed


def set_status(db: Session, contact: Contact, status: str, user: User | None) -> CrmOpportunity:
    if status not in OPP_STATUSES:
        raise StageError("Status must be open, won or lost")
    opp = get_or_create_opportunity(db, contact, user)
    if opp.status != status:
        log_activity(db, contact.id, "opportunity_status", f"Opportunity marked {status}", user, old=opp.status, new=status)
        opp.status = status
        opp.closed_at = None if status == "open" else (opp.closed_at or utcnow())
        if status == "won":
            make_client(db, contact)
    db.flush()
    return opp


def detail(db: Session, contact: Contact) -> dict:
    opp = db.scalar(select(CrmOpportunity).where(CrmOpportunity.contact_id == contact.id))
    stage = stage_of(db, contact, opp)
    company = db.get(Company, contact.company_id) if contact.company_id else None
    next_task = db.scalar(select(CrmTask).where(CrmTask.contact_id == contact.id, CrmTask.completed_at.is_(None))
                          .order_by(case((CrmTask.due_at.is_(None), 1), else_=0), CrmTask.due_at).limit(1))
    note = db.scalar(select(CrmNote).where(CrmNote.contact_id == contact.id, CrmNote.archived_at.is_(None))
                     .order_by(CrmNote.created_at.desc()).limit(1))
    open_tasks = db.scalar(select(func.count()).select_from(CrmTask).where(
        CrmTask.contact_id == contact.id, CrmTask.completed_at.is_(None))) or 0
    return {
        **card_out(contact, opp, stage, next_task, open_tasks),
        "contact_id": str(contact.id), "lead_status": contact.lead_status, "on_board": bool(opp and opp.on_board),
        "company": {"id": str(company.id), "name": company.name, "website": company.website} if company else None,
        "website": contact.website, "city": contact.city, "state": contact.state, "timezone": contact.timezone,
        "value_cents": opp.value_cents if opp else None,
        "meeting_at": opp.meeting_at.isoformat() if opp and opp.meeting_at else None,
        "created_at": contact.created_at.isoformat(),
        "next_task_detail": task_brief(next_task),
        "latest_note": {"id": str(note.id), "body": note.body, "created_at": note.created_at.isoformat()} if note else None,
    }


# --- serialisation --------------------------------------------------------------------------------

def stage_out(s: PipelineStage) -> dict:
    return {"id": str(s.id), "name": s.name, "color": s.color, "position": s.position, "lead_status": s.lead_status}


def task_brief(t: CrmTask | None) -> dict | None:
    if t is None:
        return None
    return {"id": str(t.id), "title": t.title, "task_type": t.task_type,
            "due_at": t.due_at.isoformat() if t.due_at else None, "timezone": t.timezone,
            "overdue": bool(t.due_at and t.due_at < utcnow() and not t.completed_at)}


def card_out(c: Contact, o: CrmOpportunity | None, s: PipelineStage, next_task: CrmTask | None, open_tasks: int) -> dict:
    return {
        "id": str(c.id), "first_name": c.first_name, "last_name": c.last_name, "company_name": c.company_name,
        "email": c.email, "phone": c.phone, "stage_id": str(s.id), "stage_name": s.name, "stage_color": s.color,
        "status": o.status if o else "open", "value_cents": o.value_cents if o else None,
        "next_task": task_brief(next_task), "open_tasks": open_tasks, "updated_at": c.updated_at.isoformat(),
    }
