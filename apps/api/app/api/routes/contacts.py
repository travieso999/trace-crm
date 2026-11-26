import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, require_perm, writer
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.models import (
    Campaign,
    CampaignContact,
    Company,
    Contact,
    CrmActivity,
    ScheduledMessage,
    SentMessage,
    SequenceStep,
    Tag,
    User,
)
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
from app.services import audience, cooldown, import_lists, security_alerts
from app.services import campaigns as campaign_service
from app.services import notes as note_service
from app.services.audience import AudienceFilter, contacts_query
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
from app.services.csv_import import (
    CsvError,
    export_contacts_csv,
    export_missing_names_csv,
    file_fingerprint,
    import_rows,
    parse_upload,
    previous_import,
    suggest_mapping,
)
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


def contact_filter(
    q: str | None = None,
    lead_status: str | None = None,
    verification_status: str | None = None,
    state: str | None = None,
    city: str | None = None,
    industry: str | None = None,
    tag: str | None = None,
    batch_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    not_in_campaign_id: uuid.UUID | None = None,
    participation: str | None = Query(None, pattern="^(never|previous)$"),
    company_id: uuid.UUID | None = None,
    missing_name: bool | None = None,
    missing_data: bool | None = None,
) -> AudienceFilter:
    return AudienceFilter(q=q, lead_status=lead_status, verification_status=verification_status, state=state,
                          city=city, industry=industry, tag=tag, batch_id=batch_id, campaign_id=campaign_id,
                          not_in_campaign_id=not_in_campaign_id, participation=participation, company_id=company_id,
                          missing_name=missing_name or None, missing_data=missing_data or None)


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
    # linked companies in one query, for the company_name merge value (instead of one lazy load per prospect)
    company_ids = {c.company_id for c in contacts if c.company_id and not (c.company_name or "").strip()}
    companies = {co.id: co for co in db.scalars(select(Company).where(Company.id.in_(company_ids)))} if company_ids else {}
    for c in contacts:
        o = ContactOut.model_validate(c)
        o.suppressed = c.email in suppressed
        o.campaigns = enrollments.get(c.id, [])
        co = companies.get(c.company_id)
        has_company = bool((c.company_name or "").strip()) or bool(co and (co.name or "").strip() and co.name != co.domain)
        o.missing_fields = [f for f, ok in (("first_name", bool((c.first_name or "").strip())),
                                            ("company_name", has_company)) if not ok]
        out.append(o)
    return out


@router.get("", response_model=Page[ContactOut])
def list_contacts(
    f: AudienceFilter = Depends(contact_filter),
    sort: str = "created_at",
    order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = contacts_query(f)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    col = SORTS.get(sort, Contact.created_at)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last(), Contact.id)
    items = list(db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)))
    return Page(items=_out(db, items), total=total, page=page, page_size=page_size)


@router.get("/count")
def count_contacts(f: AudienceFilter = Depends(contact_filter), _: User = Depends(reader), db: Session = Depends(get_db)):
    """How many prospects match (live count for smart filters)."""
    return {"total": audience.count(db, f)}


@router.get("/export")
def export_contacts(
    request: Request,
    f: AudienceFilter = Depends(contact_filter),
    user: User = Depends(require_perm("prospects.export")),  # bulk download of prospect data
    db: Session = Depends(get_db),
):
    """Limited per hour, logged with its filters, and every export alerts the admins."""
    if not ratelimit.hit(f"export:{user.id}", get_settings().export_limit_per_hour, 3600):
        audit(db, user, "contacts.export_refused", "contact", None, request, reason="hourly limit")
        db.commit()
        raise HTTPException(429, "Export limit reached for this hour")
    contacts = list(db.scalars(contacts_query(f).order_by(Contact.created_at).limit(100_000)))
    filters = {k: str(v) for k, v in f.to_json().items() if v}
    audit(db, user, "contacts.exported", "contact", None, request, count=len(contacts), filters=filters)
    security_alerts.notify_admins(db, f"Prospects exported by {user.display_name}",
                                  f"{user.display_name} downloaded {len(contacts)} prospect(s) as CSV"
                                  + (f" (filters: {', '.join(f'{k}={v}' for k, v in filters.items())})." if filters
                                     else " (all prospects)."))
    db.commit()
    return Response(
        export_contacts_csv(contacts),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="prospects-{utcnow():%Y%m%d}.csv"'},
    )


@router.get("/missing-names/export")
def export_missing_names(
    request: Request,
    user: User = Depends(require_perm("prospects.export")),
    db: Session = Depends(get_db),
):
    """Prospects with no first name or no company name (the Missing data tab), with only the columns needed to look
    them up (e.g. by asking Claude). The missing columns are left blank to fill in; uploading the filled file with
    "Fill in their blank fields" stores them, and the prospects drop out of this list."""
    if not ratelimit.hit(f"export:{user.id}", get_settings().export_limit_per_hour, 3600):
        audit(db, user, "contacts.export_refused", "contact", None, request, reason="hourly limit")
        db.commit()
        raise HTTPException(429, "Export limit reached for this hour")
    contacts = list(db.scalars(contacts_query(AudienceFilter(missing_data=True)).order_by(Contact.created_at).limit(100_000)))
    audit(db, user, "contacts.exported", "contact", None, request, count=len(contacts), filters={"missing_data": "true"})
    security_alerts.notify_admins(db, f"Prospects exported by {user.display_name}",
                                  f"{user.display_name} downloaded {len(contacts)} prospect(s) with a missing name or company as CSV.")
    db.commit()
    return Response(
        export_missing_names_csv(contacts),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="missing-data-{utcnow():%Y%m%d}.csv"'},
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


@router.get("/{contact_id}/outreach")
def contact_outreach(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    """Every campaign this prospect is in and every email they were sent, newest first. An email that went out soon
    after another campaign's email is flagged (``gap_days`` / ``too_close``), so overlapping lists are easy to spot."""
    contact = db.get(Contact, contact_id)
    if not contact or contact.deleted_at:
        raise HTTPException(404, "Contact not found")
    steps = (select(SequenceStep.campaign_id, func.count().label("n")).group_by(SequenceStep.campaign_id)
             .subquery())
    campaigns = [
        {"campaign_id": str(cc.campaign_id), "name": camp.name, "campaign_status": camp.status, "status": cc.status,
         "emails_sent": cc.current_step, "total_steps": n or 0, "added_at": cc.created_at,
         "next_send_at": cc.next_send_at if cc.status == "active" else None, "stop_reason": cc.stop_reason,
         "replied_at": cc.replied_at, "cooldown_days": camp.cross_campaign_cooldown_days}
        for cc, camp, n in db.execute(
            select(CampaignContact, Campaign, steps.c.n)
            .join(Campaign, Campaign.id == CampaignContact.campaign_id)
            .outerjoin(steps, steps.c.campaign_id == Campaign.id)
            .where(CampaignContact.contact_id == contact_id, Campaign.deleted_at.is_(None))
            .order_by(CampaignContact.created_at.desc()))
    ]
    rows = db.execute(
        select(SentMessage, Campaign.name, Campaign.cross_campaign_cooldown_days, SequenceStep.position)
        .outerjoin(Campaign, Campaign.id == SentMessage.campaign_id)
        .outerjoin(ScheduledMessage, ScheduledMessage.id == SentMessage.scheduled_message_id)
        .outerjoin(SequenceStep, SequenceStep.id == ScheduledMessage.sequence_step_id)
        .where(SentMessage.contact_id == contact_id, SentMessage.kind != "test")
        .order_by(SentMessage.sent_at).limit(500)
    ).all()
    emails = []
    for i, (m, name, days, position) in enumerate(rows):
        gap, other = None, None
        if m.kind == "sequence":
            prev = next((p for p in reversed(rows[:i])
                         if p[0].kind == "sequence" and p[0].campaign_id != m.campaign_id), None)
            if prev:
                gap, other = (m.sent_at - prev[0].sent_at).total_seconds() / 86400, prev[1]
        emails.append({
            "id": str(m.id), "sent_at": m.sent_at, "kind": m.kind, "subject": m.subject, "from_email": m.from_email,
            "campaign_id": str(m.campaign_id) if m.campaign_id else None, "campaign_name": name, "step": position,
            "gap_days": round(gap, 1) if gap is not None else None, "previous_campaign_name": other,
            # with the cooldown off, still flag what it would have caught
            "too_close": gap is not None and gap < (days or cooldown.DEFAULT_DAYS),
        })
    return {"campaigns": campaigns, "emails": emails[::-1]}


@router.post("/{contact_id}/notes", response_model=ActivityOut, status_code=201)
def add_note(contact_id: uuid.UUID, body: dict, user: User = Depends(writer), db: Session = Depends(get_db)):
    text = str(body.get("text", "")).strip()
    if not text or len(text) > 10000:
        raise HTTPException(422, "Note text is required (max 10,000 characters)")
    if not db.get(Contact, contact_id):
        raise HTTPException(404, "Contact not found")
    note = note_service.create(db, contact_id, text, user)  # a real note (editable, with history) + timeline entry
    db.commit()
    return ActivityOut(id=note.id, type="note", summary=note.body, data={"note_id": str(note.id)},
                       created_at=note.created_at, user_id=user.id)


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
            raise HTTPException(422, "Invalid prospect status")
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
    if name.endswith(".xls"):
        raise HTTPException(415, "Old .xls files aren't supported: save the sheet as .xlsx or .csv")
    if not name.endswith((".csv", ".txt", ".tsv", ".xlsx")):
        raise HTTPException(415, "Only .csv and .xlsx files are accepted")
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"File exceeds {limit // (1024 * 1024)} MB limit")
    return data


@router.post("/import/preview", response_model=CsvPreviewOut)
async def import_preview(file: UploadFile = File(...), _: User = Depends(writer), db: Session = Depends(get_db)):
    raw = await _read_upload(file)
    try:
        headers, rows, meta = parse_upload(file.filename or "", raw, get_settings().csv_max_rows)
    except CsvError as exc:
        raise HTTPException(422, str(exc)) from exc
    return CsvPreviewOut(
        headers=headers,
        sample_rows=rows[:10],
        total_rows=len(rows),
        suggested_mapping=suggest_mapping(headers),
        fields=IMPORT_FIELDS,
        format=meta["format"],
        sheet=meta["sheet"],
        previous_import=previous_import(db, file_fingerprint(raw)),
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
    sha = file_fingerprint(raw)
    enrolled = None
    try:
        headers, rows, meta = parse_upload(file.filename or "", raw, get_settings().csv_max_rows)
        result, touched, members = import_rows(
            db, headers, rows, opts.mapping, duplicate_strategy=opts.duplicate_strategy, tags=opts.tags,
            source=opts.source, user=user, dry_run=opts.dry_run,
        )
        batch = None
        if not opts.dry_run:
            batch = import_lists.create(db, name=opts.list_name, filename=file.filename, sha256=sha,
                                        file_format=meta["format"], total_rows=len(rows), result=result,
                                        members=members, user=user)
        if opts.campaign_id and not opts.dry_run:
            campaign = db.get(Campaign, opts.campaign_id)
            if not campaign or campaign.deleted_at:
                raise CsvError("Campaign not found")
            # the whole list joins the campaign, including prospects that were already in the CRM
            enrolled = campaign_service.enroll_contacts(db, campaign, list(dict.fromkeys(m[0] for m in members)), user)
            import_lists.link_campaign(db, campaign.id, batch.id)
    except CsvError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    except campaign_service.CampaignError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    result["previous_import"] = previous_import(db, sha)
    if opts.dry_run:
        db.rollback()
    else:
        audit(db, user, "contacts.imported", "contact", None, request, created=result["created"],
              updated=result["updated"], filename=file.filename, sha256=sha, batch_id=str(batch.id),
              campaign_id=str(opts.campaign_id) if opts.campaign_id else None)
        db.commit()
        result["contact_ids"] = touched
        result["batch_id"] = batch.id
        result["list_name"] = batch.name
        result["list_contacts"] = len({m[0] for m in members})
        result["enrollment"] = enrolled
    return CsvImportOut(**result)


@router.get("/{contact_id}/suppressed")
def contact_suppressed(contact_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    contact = db.get(Contact, contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return {"suppressed": is_suppressed(db, contact.email)}
