import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.api.routes.contacts import _filtered
from app.core.config import get_settings
from app.core.db import get_db
from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    EmailAccount,
    EmailTemplate,
    ScheduledMessage,
    SentMessage,
    SequenceStep,
    User,
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
from app.services import app_settings, enrollment, sending
from app.services import campaigns as svc
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
    out = []
    for c in campaigns:
        o = CampaignOut.model_validate(c)
        o.steps = [StepOut.model_validate(s) for s in c.steps]
        o.mailbox_ids = [m.id for m in c.mailboxes]
        o.stats = stats.get(c.id, {})
        out.append(o)
    return out


def _campaign_error(exc: svc.CampaignError) -> HTTPException:
    return HTTPException(409, {"message": str(exc), "problems": exc.problems})


def _apply(db: Session, c: Campaign, data: dict) -> None:
    for f in ("name", "description", "daily_limit", "missing_variable_policy", "variable_fallbacks",
              "require_approved_personalization", "stop_on_auto_reply", "start_at"):
        if f in data:
            setattr(c, f, data[f])
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
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    c = _get(db, campaign_id)
    stmt = (select(CampaignContact, Contact, EmailAccount.email)
            .join(Contact, Contact.id == CampaignContact.contact_id)
            .outerjoin(EmailAccount, EmailAccount.id == CampaignContact.email_account_id)
            .where(CampaignContact.campaign_id == c.id))
    if status:
        stmt = stmt.where(CampaignContact.status.in_(status.split(",")))
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(CampaignContact.created_at.desc(), CampaignContact.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    n_steps = len(c.steps)
    return {
        "items": [{
            "contact_id": str(ct.id), "email": ct.email, "first_name": ct.first_name, "last_name": ct.last_name,
            "company_name": ct.company_name, "lead_status": ct.lead_status, "status": cc.status,
            "current_step": cc.current_step, "total_steps": n_steps,
            "next_send_at": cc.next_send_at.isoformat() if cc.next_send_at else None,
            "stop_reason": cc.stop_reason, "mailbox": mbox,
            "replied_at": cc.replied_at.isoformat() if cc.replied_at else None,
            "enrolled_at": cc.created_at.isoformat(),
        } for cc, ct, mbox in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@router.post("/{campaign_id}/contacts")
def enroll(campaign_id: uuid.UUID, body: EnrollIn, request: Request, user: User = Depends(writer),
           db: Session = Depends(get_db)):
    c = _get(db, campaign_id)
    if body.contact_ids is not None:
        ids = body.contact_ids
    else:
        if not any((body.q, body.lead_status, body.verification_status, body.state, body.tag)):
            raise HTTPException(422, "Pass contact_ids or at least one filter")
        stmt = _filtered(body.q, body.lead_status, body.verification_status, body.state, body.tag, None, None)
        ids = list(db.scalars(stmt.with_only_columns(Contact.id).limit(10000)))
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
    audit(db, user, "campaign.enrolled", "campaign", c.id, request, **result)
    db.commit()
    return {**result, "summary": summary, "campaign_status": c.status}


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
