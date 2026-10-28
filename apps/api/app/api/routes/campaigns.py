import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.api.routes.imports import _get as get_import_list
from app.core.config import get_settings
from app.core.db import get_db
from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    EmailAccount,
    EmailTemplate,
    ImportBatch,
    ScheduledMessage,
    SentMessage,
    SequenceStep,
    User,
    campaign_import_batches,
)
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.base import OutgoingMessage, ProviderError, ProviderUnavailable
from app.providers.mime import new_message_id
from app.schemas.common import Message
from app.schemas.mail import (
    CampaignCreate,
    CampaignIn,
    CampaignOut,
    PreviewIn,
    StepIn,
    StepOut,
    TemplateIn,
    TemplateOut,
    TestSendIn,
)
from app.services import app_settings, campaign_reports, enrollment, import_lists, sending
from app.services import campaigns as svc
from app.services.audience import AudienceFilter, contacts_query
from app.services.contacts import get_or_create_tags, suppressed_set
from app.services.csv_import import export_contacts_csv
from app.services.normalize import InvalidEmail, normalize_email
from app.services.rendering import STANDARD_VARIABLES, MissingVariables, render_email, variables_in

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])
templates_router = APIRouter(prefix="/api/templates", tags=["templates"])

SAMPLE_CONTACT = {
    "first_name": "Alex", "last_name": "Rivera", "full_name": "Alex Rivera", "email": "alex@example.com",
    "company_name": "Rivera Bail Bonds", "city": "Austin", "state": "TX", "website": "https://riverabail.com",
    "job_title": "Owner", "industry": "Bail bonds", "phone": "(512) 555-0100", "custom_intro": "",
}


class EnrollIn(BaseModel):
    contact_ids: list[uuid.UUID] | None = Field(default=None, max_length=10000)
    # Alternatively enroll everything matching the Leads filters
    q: str | None = None
    lead_status: str | None = None
    verification_status: str | None = None
    state: str | None = None
    tag: str | None = None
    batch_id: uuid.UUID | None = None  # add a whole imported list
    filter: AudienceFilter | None = None  # smart filters (state, city, industry, list, tags, custom fields...)
    # dry_run: report per lead (ready / skipped / missing merge fields) without enrolling anyone.
    # only_ready: enroll just the leads the report marks "ready" (skip leads missing merge fields).
    dry_run: bool = False
    only_ready: bool = False


class StepsIn(BaseModel):
    steps: list[StepIn] = Field(max_length=20)


def _get(db: Session, campaign_id: uuid.UUID) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None or c.deleted_at:
        raise HTTPException(404, "Campaign not found")
    return c


def _out(db: Session, campaigns: list[Campaign]) -> list[CampaignOut]:
    stats = svc.stats(db, [c.id for c in campaigns])
    lists: dict[uuid.UUID, list[dict]] = {}
    if campaigns:
        for cid, bid, name in db.execute(
            select(campaign_import_batches.c.campaign_id, ImportBatch.id, ImportBatch.name)
            .join(ImportBatch, ImportBatch.id == campaign_import_batches.c.batch_id)
            .where(campaign_import_batches.c.campaign_id.in_([c.id for c in campaigns]),
                   ImportBatch.deleted_at.is_(None))):
            lists.setdefault(cid, []).append({"id": str(bid), "name": name})
    out = []
    for c in campaigns:
        o = CampaignOut.model_validate(c)
        o.import_lists = lists.get(c.id, [])
        o.steps = [StepOut.model_validate(s) for s in c.steps]
        o.mailbox_ids = [m.id for m in c.mailboxes]
        o.stats = stats.get(c.id, {})
        out.append(o)
    return out


def _campaign_error(exc: svc.CampaignError) -> HTTPException:
    return HTTPException(409, {"message": str(exc), "problems": exc.problems})


def _apply(db: Session, c: Campaign, data: dict) -> None:
    for f in ("name", "description", "daily_limit", "missing_variable_policy", "variable_fallbacks",
              "require_approved_personalization", "stop_on_auto_reply", "start_at", "campaign_type",
              "target_location", "industry"):
        if f in data:
            setattr(c, f, data[f].strip() or None if isinstance(data[f], str) and f != "name" else data[f])
    if "audience_filter" in data and data["audience_filter"] is not None:
        c.audience_filter = AudienceFilter.model_validate(data["audience_filter"]).to_json()
    if data.get("audience_mode"):
        if data["audience_mode"] == "dynamic" and AudienceFilter.model_validate(c.audience_filter or {}).is_empty():
            raise HTTPException(422, "A dynamic audience needs at least one filter")
        c.audience_mode = data["audience_mode"]
    if data.get("schedule"):
        sched = dict(data["schedule"])
        if sched.get("use_recipient_timezone") is None:
            sched["use_recipient_timezone"] = (c.schedule or {}).get("use_recipient_timezone", True)
        c.schedule = sched
    if "mailbox_ids" in data and data["mailbox_ids"] is not None:
        ids = list(dict.fromkeys(data["mailbox_ids"]))
        boxes = list(db.scalars(select(EmailAccount).where(EmailAccount.id.in_(ids), EmailAccount.status != "disconnected")))
        if len(boxes) != len(ids):
            raise HTTPException(422, "Unknown or disconnected email account")
        c.mailboxes = boxes


@router.get("", response_model=list[CampaignOut])
def list_campaigns(status: str | None = None, archived: bool = False, q: str | None = None,
                   _: User = Depends(reader), db: Session = Depends(get_db)):
    stmt = select(Campaign).where(Campaign.deleted_at.is_(None))
    stmt = stmt.where(Campaign.archived_at.is_not(None) if archived else Campaign.archived_at.is_(None))
    if status:
        stmt = stmt.where(Campaign.status.in_(status.split(",")))
    if q:
        stmt = stmt.where(func.lower(Campaign.name).like(f"%{q.strip().lower()}%"))
    return _out(db, list(db.scalars(stmt.order_by(Campaign.created_at.desc()))))


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignCreate, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    d = app_settings.get_section(db, "defaults")
    c = Campaign(name=body.name.strip(), status="draft", daily_limit=d.get("campaign_daily_limit", 50),
                 schedule={**(d.get("schedule") or {}), "use_recipient_timezone": True}, variable_fallbacks={},
                 created_by=user.id)
    db.add(c)
    _apply(db, c, body.model_dump(exclude_unset=True, exclude={"name"}))
    db.flush()
    if not c.steps:
        db.add(SequenceStep(campaign_id=c.id, position=1, delay_days=0, business_days=True, subject="",
                            body="", reply_in_thread=False))
        db.flush()
        db.expire(c, ["steps"])
    audit(db, user, "campaign.created", "campaign", c.id, request)
    db.commit()
    return _out(db, [c])[0]


@router.get("/variables")
def template_variable_names(_: User = Depends(reader), db: Session = Depends(get_db)):
    custom = set()
    for (fields,) in db.execute(select(Contact.custom_fields).where(Contact.deleted_at.is_(None),
                                                                   Contact.custom_fields != {}).limit(2000)):
        custom.update((fields or {}).keys())
    return {"standard": STANDARD_VARIABLES, "custom": sorted(custom)}


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return _out(db, [_get(db, campaign_id)])[0]


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(campaign_id: uuid.UUID, body: CampaignIn, request: Request, user: User = Depends(writer),
                    db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    if c.status in ("completed", "stopped"):
        raise HTTPException(409, "Finished campaigns cannot be edited; duplicate it instead")
    data = body.model_dump(exclude_unset=True)
    _apply(db, c, data)
    audit(db, user, "campaign.updated", "campaign", c.id, request, fields=sorted(data))
    db.commit()
    return _out(db, [c])[0]


@router.delete("/{campaign_id}", response_model=Message)
def delete_campaign(campaign_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    if c.status in ("active", "scheduled", "paused", "error"):
        svc.stop(db, c)
    c.deleted_at = utcnow()
    audit(db, user, "campaign.deleted", "campaign", c.id, request)
    db.commit()
    return Message(message="Campaign deleted")


@router.put("/{campaign_id}/steps", response_model=CampaignOut)
def put_steps(campaign_id: uuid.UUID, body: StepsIn, request: Request, user: User = Depends(writer),
              db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    try:
        svc.replace_steps(db, c, [s.model_dump() for s in body.steps])
    except svc.CampaignError as exc:
        raise _campaign_error(exc) from exc
    audit(db, user, "campaign.steps_updated", "campaign", c.id, request, steps=len(body.steps))
    db.commit()
    db.refresh(c)
    return _out(db, [c])[0]


def _action(name: str):
    def handler(campaign_id: uuid.UUID, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
        c = _get(db, campaign_id)
        try:
            if name == "start":
                svc.start(db, c, user)
            elif name == "pause":
                svc.pause(db, c)
            elif name == "resume":
                svc.resume(db, c)
            elif name == "stop":
                svc.stop(db, c)
            elif name == "archive":
                if c.status in ("active", "scheduled"):
                    raise svc.CampaignError("Pause or stop the campaign before archiving it")
                c.archived_at = utcnow()
            elif name == "unarchive":
                c.archived_at = None
        except svc.CampaignError as exc:
            raise _campaign_error(exc) from exc
        audit(db, user, f"campaign.{name}", "campaign", c.id, request)
        db.commit()
        return _out(db, [c])[0]

    handler.__name__ = f"campaign_{name}"
    return handler


for _name in ("start", "pause", "resume", "stop", "archive", "unarchive"):
    router.post(f"/{{campaign_id}}/{_name}", response_model=CampaignOut)(_action(_name))


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut, status_code=201)
def duplicate_campaign(campaign_id: uuid.UUID, request: Request, user: User = Depends(writer),
                       db: Session = Depends(get_db)):
    copy = svc.duplicate(db, _get(db, campaign_id), user)
    audit(db, user, "campaign.duplicated", "campaign", copy.id, request, source=str(campaign_id))
    db.commit()
    db.refresh(copy)
    return _out(db, [copy])[0]


@router.get("/{campaign_id}/preflight")
def preflight(campaign_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    return {"problems": svc.preflight(db, c), "variables": svc.variable_report(db, c)}


@router.get("/{campaign_id}/contacts")
def campaign_contacts(
    campaign_id: uuid.UUID,
    status: str | None = None,
    q: str | None = None,
    state: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    c = _get(db, campaign_id)
    last_sent = (select(SentMessage.contact_id, func.max(SentMessage.sent_at).label("at"))
                 .where(SentMessage.campaign_id == c.id).group_by(SentMessage.contact_id).subquery())
    stmt = (select(CampaignContact, Contact, EmailAccount.email, last_sent.c.at)
            .join(Contact, Contact.id == CampaignContact.contact_id)
            .outerjoin(EmailAccount, EmailAccount.id == CampaignContact.email_account_id)
            .outerjoin(last_sent, last_sent.c.contact_id == Contact.id)
            .where(CampaignContact.campaign_id == c.id))
    # removed prospects are hidden unless asked for (they stay in Prospects with their history)
    stmt = stmt.where(CampaignContact.status.in_(status.split(","))) if status else \
        stmt.where(CampaignContact.status != "removed")
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    if state:
        stmt = stmt.where(func.upper(Contact.state).in_([x.strip().upper() for x in state.split(",") if x.strip()]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(CampaignContact.created_at.desc(), CampaignContact.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    suppressed = suppressed_set(db, [ct.email for _, ct, _, _ in rows])
    n_steps = len(c.steps)
    return {
        "items": [{
            "contact_id": str(ct.id), "email": ct.email, "first_name": ct.first_name, "last_name": ct.last_name,
            "company_name": ct.company_name, "phone": ct.phone, "state": ct.state, "city": ct.city,
            "lead_status": ct.lead_status, "verification_status": ct.verification_status,
            "suppressed": ct.email in suppressed, "status": cc.status,
            "current_step": cc.current_step, "total_steps": n_steps,
            "next_send_at": cc.next_send_at.isoformat() if cc.next_send_at else None,
            "stop_reason": cc.stop_reason, "mailbox": mbox,
            "replied_at": cc.replied_at.isoformat() if cc.replied_at else None,
            "last_sent_at": sent_at.isoformat() if sent_at else None,
            "paused_at": cc.paused_at.isoformat() if cc.paused_at else None,
            "enrolled_at": cc.created_at.isoformat(),
        } for cc, ct, mbox, sent_at in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post("/{campaign_id}/contacts")
def enroll(campaign_id: uuid.UUID, body: EnrollIn, request: Request, user: User = Depends(writer),
           db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    if body.contact_ids is not None:
        ids = body.contact_ids
    else:
        f = body.filter or AudienceFilter(q=body.q, lead_status=body.lead_status,
                                          verification_status=body.verification_status, state=body.state, tag=body.tag)
        if body.batch_id:
            get_import_list(db, body.batch_id)  # 404 for a deleted or unknown list
            f = f.model_copy(update={"batch_id": body.batch_id})
        if f.is_empty():
            raise HTTPException(422, "Pass contact_ids, an imported list or at least one filter")
        ids = list(db.scalars(contacts_query(f).with_only_columns(Contact.id).order_by(Contact.created_at).limit(10000)))
    if c.status in ("completed", "stopped") or c.archived_at:
        raise _campaign_error(svc.CampaignError("Cannot add prospects to a finished or archived campaign"))
    rows = enrollment.report(db, c, ids)
    summary = enrollment.summarize(rows)
    if body.dry_run:
        return {"dry_run": True, "summary": summary, "leads": rows[:2000], "campaign_status": c.status}
    if body.only_ready:
        ids = [uuid.UUID(r["contact_id"]) for r in rows if r["status"] == "ready"]
    try:
        result = svc.enroll_contacts(db, c, ids, user)
    except svc.CampaignError as exc:
        raise _campaign_error(exc) from exc
    if body.only_ready:
        result["skipped_missing_fields"] = summary["missing_fields"]
    if body.batch_id:
        import_lists.link_campaign(db, c.id, body.batch_id)
    audit(db, user, "campaign.enrolled", "campaign", c.id, request, **result)
    db.commit()
    return {**result, "summary": summary, "campaign_status": c.status}


@router.get("/{campaign_id}/recipients-summary")
def recipients_summary(campaign_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    """Who is in the campaign and who will actually be emailed (shown before sending)."""
    return svc.recipient_summary(db, _get(db, campaign_id))


def _step(c: Campaign, step_id: uuid.UUID) -> SequenceStep:
    step = next((s for s in c.steps if s.id == step_id), None)
    if step is None:
        raise HTTPException(404, "Email not found in this campaign")
    return step


@router.get("/{campaign_id}/steps/{step_id}/audience")
def step_audience(campaign_id: uuid.UUID, step_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    return svc.step_audience(db, c, _step(c, step_id))


@router.post("/{campaign_id}/steps/{step_id}/send-now")
def send_step_now(campaign_id: uuid.UUID, step_id: uuid.UUID, request: Request, user: User = Depends(writer),
                  db: Session = Depends(get_db)):
    """Send this email now to every prospect waiting for it (eligible non-responders)."""
    c = _get(db, campaign_id)
    step = _step(c, step_id)
    try:
        n = svc.send_step_now(db, c, step)
    except svc.CampaignError as exc:
        raise _campaign_error(exc) from exc
    audit(db, user, "campaign.step_sent_now", "campaign", c.id, request, step=step.position, prospects=n)
    db.commit()
    return {"queued": n, "message": f"{n} prospect(s) queued. Emails go out within the sending window and daily limits."}


@router.get("/{campaign_id}/steps-report")
def steps_report(campaign_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return campaign_reports.steps_report(db, _get(db, campaign_id))


@router.get("/{campaign_id}/steps/{step_id}/messages")
def step_messages(campaign_id: uuid.UUID, step_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    return campaign_reports.step_messages(db, c, _step(c, step_id).id)


@router.get("/{campaign_id}/contacts/{contact_id}/history")
def contact_history(campaign_id: uuid.UUID, contact_id: uuid.UUID, _: User = Depends(reader),
                    db: Session = Depends(get_db)):
    return campaign_reports.contact_history(db, _get(db, campaign_id), contact_id)


@router.get("/{campaign_id}/analytics")
def campaign_analytics(campaign_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return campaign_reports.analytics(db, _get(db, campaign_id))


class CampaignBulkIn(BaseModel):
    action: str = Field(pattern="^(remove|pause|resume|stop|copy|move|add_tag)$")
    contact_ids: list[uuid.UUID] | None = Field(default=None, max_length=10000)
    # "select all": every prospect in the campaign matching these (instead of contact_ids)
    all_matching: bool = False
    status: str | None = None
    q: str | None = None
    target_campaign_id: uuid.UUID | None = None  # copy / move
    tag: str | None = Field(default=None, max_length=80)  # add_tag


def _campaign_contact_ids(db: Session, c: Campaign, status: str | None, q: str | None) -> list[uuid.UUID]:
    stmt = (select(CampaignContact.contact_id).join(Contact, Contact.id == CampaignContact.contact_id)
            .where(CampaignContact.campaign_id == c.id))
    stmt = stmt.where(CampaignContact.status.in_(status.split(","))) if status else \
        stmt.where(CampaignContact.status != "removed")
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    return list(db.scalars(stmt.limit(10000)))


@router.post("/{campaign_id}/contacts/bulk")
def campaign_contacts_bulk(campaign_id: uuid.UUID, body: CampaignBulkIn, request: Request,
                           user: User = Depends(writer), db: Session = Depends(get_db)):
    """Bulk actions on prospects in this campaign. Nothing here deletes a prospect from Prospects."""
    c = _get(db, campaign_id)
    if body.all_matching:
        ids = _campaign_contact_ids(db, c, body.status, body.q)
    elif body.contact_ids:
        ids = list(dict.fromkeys(body.contact_ids))
    else:
        raise HTTPException(422, "Select at least one prospect")
    try:
        if body.action == "remove":
            n = sum(1 for cid in ids if svc.remove_contact(db, c, cid))
            result = {"affected": n, "message": f"{n} prospect(s) removed from this campaign (still in Prospects)"}
        elif body.action in ("pause", "resume", "stop"):
            fn = {"pause": svc.pause_contacts, "resume": svc.resume_contacts, "stop": svc.stop_contacts}[body.action]
            n = fn(db, c, ids)
            verb = {"pause": "paused", "resume": "resumed", "stop": "will get no more emails from this campaign"}
            result = {"affected": n, "message": f"{n} prospect(s) {verb[body.action]}"}
        elif body.action in ("copy", "move"):
            if not body.target_campaign_id:
                raise HTTPException(422, "Pick the campaign to copy or move to")
            target = _get(db, body.target_campaign_id)
            r = svc.copy_contacts(db, c, target, ids, user, move=body.action == "move")
            result = {**r, "affected": r["enrolled"],
                      "message": f"{r['enrolled']} prospect(s) added to “{target.name}”"
                                 + (" and removed from this campaign" if body.action == "move" else "")}
        else:  # add_tag
            if not (body.tag or "").strip():
                raise HTTPException(422, "Tag name required")
            tag = get_or_create_tags(db, [body.tag.strip()])[0]
            in_campaign = set(db.scalars(select(CampaignContact.contact_id).where(
                CampaignContact.campaign_id == c.id, CampaignContact.contact_id.in_(ids))))
            n = 0
            for contact in db.scalars(select(Contact).where(Contact.id.in_(in_campaign))):
                if tag not in contact.tags:
                    contact.tags = [*contact.tags, tag]
                    n += 1
            result = {"affected": n, "message": f"Tag “{tag.name}” added to {n} prospect(s)"}
    except svc.CampaignError as exc:
        raise _campaign_error(exc) from exc
    audit(db, user, f"campaign.contacts_{body.action}", "campaign", c.id, request, count=result["affected"],
          target=str(body.target_campaign_id) if body.target_campaign_id else None)
    db.commit()
    return result


@router.get("/{campaign_id}/contacts/export")
def export_campaign_contacts(campaign_id: uuid.UUID, request: Request, status: str | None = None, q: str | None = None,
                             user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    ids = _campaign_contact_ids(db, c, status, q)
    contacts = list(db.scalars(select(Contact).where(Contact.id.in_(ids)).order_by(Contact.email)))
    audit(db, user, "campaign.contacts_exported", "campaign", c.id, request, count=len(contacts))
    db.commit()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in c.name.lower()).strip("-")[:60] or "campaign"
    return Response(export_contacts_csv(contacts), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{safe}-{utcnow():%Y%m%d}.csv"'})


@router.get("/{campaign_id}/preview/{contact_id}")
def preview_for_lead(campaign_id: uuid.UUID, contact_id: uuid.UUID, _: User = Depends(reader),
                     db: Session = Depends(get_db)):
    """Personalized subject + body of every step for one lead. Read-only; enrollment not required."""
    c = _get(db, campaign_id)
    contact = db.get(Contact, contact_id)
    if contact is None or contact.deleted_at:
        raise HTTPException(404, "Contact not found")
    return enrollment.preview_lead(db, c, contact)


@router.delete("/{campaign_id}/contacts/{contact_id}", response_model=Message)
def remove_contact(campaign_id: uuid.UUID, contact_id: uuid.UUID, request: Request, user: User = Depends(writer),
                   db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    if not svc.remove_contact(db, c, contact_id):
        raise HTTPException(404, "Prospect is not in this campaign")
    audit(db, user, "campaign.contact_removed", "campaign", c.id, request, contact_id=str(contact_id))
    db.commit()
    return Message(message="Prospect removed from campaign")


@router.get("/{campaign_id}/messages")
def campaign_messages(
    campaign_id: uuid.UUID,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    """Send queue and history for the campaign (scheduled messages joined with the sent copy)."""
    c = _get(db, campaign_id)
    stmt = (select(ScheduledMessage, Contact.email, SequenceStep.position, SentMessage.subject, SentMessage.sent_at,
                   SentMessage.id, EmailAccount.email, EmailAccount.id, EmailAccount.provider)
            .join(CampaignContact, CampaignContact.id == ScheduledMessage.campaign_contact_id)
            .join(Contact, Contact.id == CampaignContact.contact_id)
            .join(SequenceStep, SequenceStep.id == ScheduledMessage.sequence_step_id)
            .outerjoin(SentMessage, SentMessage.scheduled_message_id == ScheduledMessage.id)
            .outerjoin(EmailAccount, EmailAccount.id == ScheduledMessage.email_account_id)
            .where(ScheduledMessage.campaign_id == c.id))
    if status:
        stmt = stmt.where(ScheduledMessage.status.in_(status.split(",")))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(ScheduledMessage.updated_at.desc()).offset((page - 1) * page_size)
                      .limit(page_size)).all()
    return {
        "items": [{
            "id": str(sm.id), "status": sm.status, "to": email, "step": pos, "subject": subj,
            "due_at": sm.due_at.isoformat(), "sent_at": sent_at.isoformat() if sent_at else None,
            "sent_message_id": str(sent_id) if sent_id else None, "mailbox": mbox,
            "mailbox_id": str(mbox_id) if mbox_id else None, "mailbox_provider": provider,
            "attempts": sm.attempts,
            "last_error": sm.last_error,
        } for sm, email, pos, subj, sent_at, sent_id, mbox, mbox_id, provider in rows],
        "total": total, "page": page, "page_size": page_size,
    }


def _preview_vars(db: Session, body: PreviewIn) -> tuple[dict, Campaign | None, str]:
    campaign = _get(db, body.campaign_id) if body.campaign_id else None
    contact = None
    if body.contact_id:
        contact = db.get(Contact, body.contact_id)
        if contact is None or contact.deleted_at:
            raise HTTPException(404, "Contact not found")
    elif campaign:
        contact = db.scalar(select(Contact).join(CampaignContact, CampaignContact.contact_id == Contact.id)
                            .where(CampaignContact.campaign_id == campaign.id).order_by(CampaignContact.created_at)
                            .limit(1))
    if contact:
        return sending.contact_variables(db, contact), campaign, contact.email
    return dict(SAMPLE_CONTACT), campaign, "sample prospect"


@router.post("/preview")
def preview(body: PreviewIn, _: User = Depends(reader), db: Session = Depends(get_db)):
    vars_, campaign, who = _preview_vars(db, body)
    unsub = f"{get_settings().public_url.rstrip('/')}/u/preview"
    try:
        footer = sending.footer_text(db, unsub)
    except MissingVariables:
        footer = None
    try:
        r = render_email(body.subject, body.body, vars_,
                         policy=campaign.missing_variable_policy if campaign else "block",
                         fallbacks=campaign.variable_fallbacks if campaign else None, footer=footer)
    except MissingVariables as exc:
        return {"ok": False, "missing": exc.names, "recipient": who,
                "variables_used": sorted(variables_in(body.subject) | variables_in(body.body))}
    return {"ok": True, "subject": r.subject, "html": r.html, "text": r.text, "recipient": who, "missing": [],
            "variables_used": sorted(variables_in(body.subject) | variables_in(body.body))}


@router.post("/test-send", response_model=Message)
def test_send(body: TestSendIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    """Send one rendered test email. Recipients are limited by ``sending.test_recipients`` (ALLOWED_TEST_RECIPIENTS
    for real mailboxes; app users too for sandbox mailboxes)."""
    try:
        to = normalize_email(body.to_email)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from exc
    mailbox = db.get(EmailAccount, body.email_account_id)
    if mailbox is None or mailbox.status != "connected":
        raise HTTPException(409, "Choose a connected email account")
    if to not in sending.test_recipients(db, mailbox):
        raise HTTPException(403, sending.TEST_RECIPIENT_ERROR)
    vars_, campaign, _who = _preview_vars(db, body)
    try:
        r = render_email(body.subject, body.body, vars_,
                         policy=campaign.missing_variable_policy if campaign else "block",
                         fallbacks=campaign.variable_fallbacks if campaign else None,
                         footer=sending.footer_text(db, f"{get_settings().public_url.rstrip('/')}/u/test"))
    except MissingVariables as exc:
        raise HTTPException(422, str(exc)) from exc
    rfc_id = new_message_id(mailbox.email)
    out = OutgoingMessage(from_email=mailbox.email, from_name=mailbox.display_name, to_email=to,
                          subject=f"[TEST] {r.subject}", text=r.text, html=r.html, rfc_message_id=rfc_id)
    try:
        result = get_provider(mailbox, "test").send(out)
    except ProviderUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, f"Send failed: {exc}") from exc
    db.add(SentMessage(email_account_id=mailbox.id, campaign_id=campaign.id if campaign else None,
                       sent_by_user_id=user.id, kind="test", to_email=to, from_email=mailbox.email,
                       subject=out.subject, body_html=r.html, rfc_message_id=rfc_id,
                       provider_message_id=result.provider_message_id, provider_thread_id=result.provider_thread_id))
    audit(db, user, "campaign.test_send", "email_account", mailbox.id, request, to=to)
    db.commit()
    where = " (sandbox: written to the local outbox)" if mailbox.provider == "sandbox" else ""
    return Message(message=f"Test email sent to {to}{where}")


# --- templates ------------------------------------------------------------------------------------

@templates_router.get("", response_model=list[TemplateOut])
def list_templates(_: User = Depends(reader), db: Session = Depends(get_db)):
    return list(db.scalars(select(EmailTemplate).order_by(func.lower(EmailTemplate.name))))


@templates_router.post("", response_model=TemplateOut, status_code=201)
def create_template(body: TemplateIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = EmailTemplate(name=body.name.strip(), subject=body.subject, body=body.body)
    db.add(t)
    db.commit()
    return t


@templates_router.put("/{template_id}", response_model=TemplateOut)
def update_template(template_id: uuid.UUID, body: TemplateIn, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = db.get(EmailTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    t.name, t.subject, t.body = body.name.strip(), body.subject, body.body
    db.commit()
    return t


@templates_router.delete("/{template_id}", response_model=Message)
def delete_template(template_id: uuid.UUID, user: User = Depends(writer), db: Session = Depends(get_db)):
    t = db.get(EmailTemplate, template_id)
    if t is None:
        raise HTTPException(404, "Template not found")
    db.delete(t)
    db.commit()
    return Message(message="Template deleted")
