import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.config import get_settings
from app.core.db import get_db
from app.models import Campaign, CampaignContact, Contact, CrmActivity, Tag, User, contact_tags
from app.models.base import utcnow
from app.schemas.common import BulkResult, Message, Page
from app.schemas.contacts import (
    ActivityOut,
    BulkContactAction,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    CsvImportIn,
    CsvImportOut,
    CsvPreviewOut,
    TagOut,
)
from app.services import campaigns as campaign_service
from app.services.contacts import (
    IMPORT_FIELDS,
    apply_fields,
    find_active_contact,
    get_or_create_tags,
    is_suppressed,
    log_activity,
    set_lead_status,
    suppressed_set,
)
from app.services.csv_import import CsvError, export_contacts_csv, import_rows, parse_csv, suggest_mapping
from app.services.normalize import InvalidEmail, normalize_email

router = APIRouter(prefix="/api/contacts", tags=["contacts"])

SORTS = {
    "created_at": Contact.created_at,
    "email": Contact.email,
    "last_name": Contact.last_name,
    "company_name": Contact.company_name,
    "state": Contact.state,
    "last_contacted_at": Contact.last_contacted_at,
    "lead_status": Contact.lead_status,
}


def _filtered(q, lead_status, verification_status, state, tag, campaign_id, company_id):
    stmt = select(Contact).where(Contact.deleted_at.is_(None))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.email).like(like),
                func.lower(Contact.first_name).like(like),
                func.lower(Contact.last_name).like(like),
                func.lower(Contact.company_name).like(like),
                func.lower(Contact.city).like(like),
            )
        )
    if lead_status:
        stmt = stmt.where(Contact.lead_status.in_(lead_status.split(",")))
    if verification_status:
        stmt = stmt.where(Contact.verification_status == verification_status)
    if state:
        stmt = stmt.where(Contact.state.in_([s.upper() for s in state.split(",")]))
    if tag:
        stmt = stmt.where(Contact.id.in_(select(contact_tags.c.contact_id).join(Tag).where(Tag.name == tag)))
    if campaign_id:
        stmt = stmt.where(Contact.id.in_(select(CampaignContact.contact_id).where(CampaignContact.campaign_id == campaign_id)))
    if company_id:
        stmt = stmt.where(Contact.company_id == company_id)
    return stmt


def _out(db: Session, contacts: list[Contact]) -> list[ContactOut]:
    if not contacts:
        return []
    suppressed = suppressed_set(db, [c.email for c in contacts])
    enrollments: dict[uuid.UUID, list[dict]] = {}
    rows = db.execute(
        select(CampaignContact.contact_id, Campaign.id, Campaign.name, CampaignContact.status)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .where(CampaignContact.contact_id.in_([c.id for c in contacts]), Campaign.deleted_at.is_(None))
    )
    for cid, camp_id, name, st in rows:
        enrollments.setdefault(cid, []).append({"id": str(camp_id), "name": name, "status": st})
    out = []
    for c in contacts:
        o = ContactOut.model_validate(c)
        o.suppressed = c.email in suppressed
        o.campaigns = enrollments.get(c.id, [])
        out.append(o)
    return out


@router.get("", response_model=Page[ContactOut])
def list_contacts(
    q: str | None = None,
    lead_status: str | None = None,
    verification_status: str | None = None,
    state: str | None = None,
    tag: str | None = None,
    campaign_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    sort: str = "created_at",
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = _filtered(q, lead_status, verification_status, state, tag, campaign_id, company_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = SORTS.get(sort, Contact.created_at)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Contact.id)
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return Page(items=_out(db, items), total=total, page=page, page_size=page_size)


@router.get("/export")
def export_contacts(
    q: str | None = None,
    lead_status: str | None = None,
    verification_status: str | None = None,
    state: str | None = None,
    tag: str | None = None,
    campaign_id: uuid.UUID | None = None,
    company_id: uuid.UUID | None = None,
    request: Request = None,
    user: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = _filtered(q, lead_status, verification_status, state, tag, campaign_id, company_id).order_by(Contact.created_at)
    contacts = list(db.scalars(stmt.limit(100_000)))
    audit(db, user, "contacts.exported", "contact", None, request, count=len(contacts))
    db.commit()
    return Response(
        export_contacts_csv(contacts),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="contacts-{utcnow():%Y%m%d}.csv"'},
    )


@router.get("/tags", response_model=list[TagOut])
def list_tags(_: User = Depends(reader), db: Session = Depends(get_db)):
    return list(db.scalars(select(Tag).order_by(Tag.name)))


@router.get("/fields")
def import_fields(_: User = Depends(reader)):
    return {"fields": IMPORT_FIELDS}


@router.post("", response_model=ContactOut, status_code=201)
def create_contact(body: ContactCreate, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    try:
        email = normalize_email(body.email)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from exc
    if find_active_contact(db, email):
        raise HTTPException(409, "A contact with this email already exists")
    contact = Contact(email=email, custom_fields={})
    db.add(contact)
    apply_fields(db, contact, body.model_dump(exclude_unset=True))
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A contact with this email already exists") from exc
    log_activity(db, contact.id, "created", "Contact created manually", user)
    audit(db, user, "contact.created", "contact", contact.id, request)
    db.commit()
    db.refresh(contact)
    return _out(db, [contact])[0]


@router.get("/{contact_id}", response_model=ContactOut)
def get_contact(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(404, "Contact not found")
    return _out(db, [contact])[0]


@router.patch("/{contact_id}", response_model=ContactOut)
def update_contact(contact_id: uuid.UUID, body: ContactUpdate, request: Request, user: User = Depends(writer),
                   db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(404, "Contact not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("email"):
        try:
            new_email = normalize_email(data["email"])
        except InvalidEmail as exc:
            raise HTTPException(422, str(exc)) from exc
        other = find_active_contact(db, new_email)
        if other and other.id != contact.id:
            raise HTTPException(409, "Another contact already uses this email")
    status = data.pop("lead_status", None)
    apply_fields(db, contact, data)
    if status:
        set_lead_status(db, contact, status, user)
        if status == "not_interested":
            campaign_service.stop_contact_everywhere(db, contact.id, "not_interested")
    audit(db, user, "contact.updated", "contact", contact.id, request, fields=sorted(data.keys()))
    db.commit()
    db.refresh(contact)
    return _out(db, [contact])[0]


@router.delete("/{contact_id}", response_model=Message)
def delete_contact(contact_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(404, "Contact not found")
    campaign_service.stop_contact_everywhere(db, contact.id, "removed", status="removed")
    contact.deleted_at = utcnow()
    audit(db, user, "contact.deleted", "contact", contact.id, request)
    db.commit()
    return Message(message="Contact deleted")


@router.get("/{contact_id}/activities", response_model=list[ActivityOut])
def contact_activities(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return list(
        db.scalars(select(CrmActivity).where(CrmActivity.contact_id == contact_id).order_by(CrmActivity.created_at.desc()).limit(200))
    )


@router.post("/{contact_id}/notes", response_model=ActivityOut, status_code=201)
def add_note(contact_id: uuid.UUID, body: dict, user: User = Depends(writer), db: Session = Depends(get_db)):
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 10000:
        raise HTTPException(422, "Note text is required (max 10,000 characters)")
    if not db.get(Contact, contact_id):
        raise HTTPException(404, "Contact not found")
    act = CrmActivity(contact_id=contact_id, user_id=user.id, type="note", summary=text, data={})
    db.add(act)
    db.commit()
    return act


@router.post("/bulk", response_model=BulkResult)
def bulk_action(body: BulkContactAction, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    contacts = list(db.scalars(select(Contact).where(Contact.id.in_(body.ids), Contact.deleted_at.is_(None))))
    n = 0
    if body.action == "delete":
        for c in contacts:
            campaign_service.stop_contact_everywhere(db, c.id, "removed", status="removed")
            c.deleted_at = utcnow()
            n += 1
    elif body.action == "set_status":
        from app.models.crm import LEAD_STATUSES

        if body.value not in LEAD_STATUSES:
            raise HTTPException(422, "Invalid lead status")
        for c in contacts:
            if set_lead_status(db, c, body.value, user, reason="bulk"):
                n += 1
            if body.value == "not_interested":
                campaign_service.stop_contact_everywhere(db, c.id, "not_interested")
    elif body.action == "set_verification":
        if body.value not in ("unknown", "valid", "risky", "invalid"):
            raise HTTPException(422, "Invalid verification status")
        for c in contacts:
            c.verification_status = body.value
            n += 1
    elif body.action in ("add_tag", "remove_tag"):
        if not body.value:
            raise HTTPException(422, "Tag name required")
        tag = get_or_create_tags(db, [body.value])[0]
        for c in contacts:
            has = any(t.id == tag.id for t in c.tags)
            if body.action == "add_tag" and not has:
                c.tags = [*c.tags, tag]
                n += 1
            elif body.action == "remove_tag" and has:
                c.tags = [t for t in c.tags if t.id != tag.id]
                n += 1
    elif body.action == "assign_campaign":
        campaign = db.get(Campaign, body.value) if body.value else None
        if not campaign or campaign.deleted_at:
            raise HTTPException(404, "Campaign not found")
        n = campaign_service.enroll_contacts(db, campaign, [c.id for c in contacts], user)["enrolled"]
    audit(db, user, f"contacts.bulk.{body.action}", "contact", None, request, count=n, value=body.value)
    db.commit()
    return BulkResult(updated=n)


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().csv_max_bytes
    name = (file.filename or "").lower()
    if not name.endswith((".csv", ".txt", ".tsv")):
        raise HTTPException(415, "Only .csv files are accepted")
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {limit // (1024 * 1024)} MB limit")
    return data


@router.post("/import/preview", response_model=CsvPreviewOut)
async def import_preview(file: UploadFile = File(...), _: User = Depends(writer)):
    raw = await _read_upload(file)
    try:
        headers, rows = parse_csv(raw, get_settings().csv_max_rows)
    except CsvError as exc:
        raise HTTPException(422, str(exc)) from exc
    return CsvPreviewOut(
        headers=headers,
        sample_rows=rows[:10],
        total_rows=len(rows),
        suggested_mapping=suggest_mapping(headers),
        fields=IMPORT_FIELDS,
    )


@router.post("/import", response_model=CsvImportOut)
async def import_commit(
    request: Request,
    file: UploadFile = File(...),
    options: str = Form(...),
    user: User = Depends(writer),
    db: Session = Depends(get_db),
):
    try:
        opts = CsvImportIn.model_validate(json.loads(options))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, f"Invalid import options: {exc}") from exc
    raw = await _read_upload(file)
    try:
        headers, rows = parse_csv(raw, get_settings().csv_max_rows)
        result, touched = import_rows(
            db, headers, rows, opts.mapping, duplicate_strategy=opts.duplicate_strategy, tags=opts.tags,
            source=opts.source, user=user, dry_run=opts.dry_run,
        )
        if opts.campaign_id and not opts.dry_run:
            campaign = db.get(Campaign, opts.campaign_id)
            if not campaign or campaign.deleted_at:
                raise CsvError("Campaign not found")
            campaign_service.enroll_contacts(db, campaign, touched, user)
    except CsvError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    if opts.dry_run:
        db.rollback()
    else:
        audit(db, user, "contacts.imported", "contact", None, request, created=result["created"],
              updated=result["updated"], filename=file.filename)
        db.commit()
    return CsvImportOut(**result)


@router.get("/{contact_id}/suppressed")
def contact_suppressed(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return {"suppressed": is_suppressed(db, contact.email)}
