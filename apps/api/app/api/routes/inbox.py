import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import audit, reader, writer
from app.core.db import get_db
from app.models import Campaign, Contact, Conversation, EmailAccount, ReceivedMessage, SentMessage, User
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.base import OutgoingMessage, ProviderError, ProviderUnavailable
from app.providers.mime import new_message_id
from app.schemas.mail import ReplyIn
from app.services import inbox as inbox_service
from app.services.contacts import log_activity
from app.services.inbound import latest_text
from app.services.rendering import text_to_html

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

INTENTS = ("positive", "neutral", "negative", "meeting", "not_now", "wrong_person")


class ConversationPatch(BaseModel):
    is_read: bool | None = None
    intent: str | None = Field(default=None, pattern=f"^({'|'.join(INTENTS)})$")


class DeleteIn(BaseModel):
    conversation_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


def _get(db: Session, conv_id: uuid.UUID) -> Conversation:
    conv = db.get(Conversation, conv_id)
    if conv is None or conv.hidden_at is not None:
        raise HTTPException(404, "Conversation not found")
    return conv


def _unread(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Conversation).where(
        *inbox_service.visible(), Conversation.is_read.is_(False))) or 0


def _filtered(view: str, mailbox_id, campaign_id, q):
    stmt = select(Conversation).where(*inbox_service.visible())
    if view == "unread":
        stmt = stmt.where(Conversation.is_read.is_(False))
    elif view in INTENTS:
        stmt = stmt.where(Conversation.intent == view)
    if mailbox_id:
        stmt = stmt.where(Conversation.email_account_id == mailbox_id)
    if campaign_id:
        stmt = stmt.where(Conversation.campaign_id == campaign_id)
    if q:
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Conversation.subject).like(like),
            Conversation.id.in_(select(ReceivedMessage.conversation_id).where(
                or_(func.lower(ReceivedMessage.from_email).like(like), func.lower(ReceivedMessage.body_text).like(like)))),
            Conversation.contact_id.in_(select(Contact.id).where(or_(
                func.lower(Contact.first_name).like(like), func.lower(Contact.last_name).like(like),
                func.lower(Contact.company_name).like(like)))),
        ))
    return stmt


@router.get("")
def list_conversations(
    view: str = "all",
    mailbox_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    q: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = _filtered(view, mailbox_id, campaign_id, q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    convs = list(db.scalars(stmt.order_by(Conversation.last_message_at.desc().nulls_last(), Conversation.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    ids = [c.id for c in convs]
    latest: dict[uuid.UUID, ReceivedMessage] = {}
    if ids:
        sub = (select(ReceivedMessage).where(ReceivedMessage.conversation_id.in_(ids), ReceivedMessage.kind != "bounce")
               .order_by(ReceivedMessage.conversation_id, ReceivedMessage.received_at.desc())
               .distinct(ReceivedMessage.conversation_id))
        latest = {m.conversation_id: m for m in db.scalars(sub)}
    contacts = {c.id: c for c in db.scalars(select(Contact).where(Contact.id.in_([c.contact_id for c in convs if c.contact_id])))}
    mailboxes = dict(db.execute(select(EmailAccount.id, EmailAccount.email).where(
        EmailAccount.id.in_({c.email_account_id for c in convs}))).all()) if convs else {}
    campaigns = dict(db.execute(select(Campaign.id, Campaign.name).where(
        Campaign.id.in_({c.campaign_id for c in convs if c.campaign_id}))).all()) if convs else {}
    items = []
    for c in convs:
        m, ct = latest.get(c.id), contacts.get(c.contact_id)
        items.append({
            "id": str(c.id), "subject": c.subject, "is_read": c.is_read, "intent": c.intent,
            "last_message_at": c.last_message_at.isoformat() if c.last_message_at else None,
            "mailbox": mailboxes.get(c.email_account_id), "campaign": campaigns.get(c.campaign_id),
            "contact": {"id": str(ct.id), "email": ct.email, "first_name": ct.first_name, "last_name": ct.last_name,
                        "company_name": ct.company_name, "lead_status": ct.lead_status} if ct else None,
            "from_email": m.from_email if m else None, "from_name": m.from_name if m else None,
            "kind": m.kind if m else None,
            "snippet": (latest_text(m.body_text or "") or "")[:180] if m else "",
        })
    return {"items": items, "total": total, "page": page, "page_size": page_size, "unread": _unread(db)}


@router.get("/unread-count")
def unread_count(_: User = Depends(reader), db: Session = Depends(get_db)):
    return {"unread": _unread(db)}


@router.post("/delete")
def delete_conversations(body: DeleteIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    """Remove conversations from the inbox (soft delete). Messages, lead history and analytics are kept, nothing is
    deleted in Gmail, and later syncs keep them hidden."""
    convs = inbox_service.by_ids(db, body.conversation_ids)
    hidden = inbox_service.hide(db, convs, "deleted", user)
    for c in hidden:
        audit(db, user, "inbox.conversation_deleted", "conversation", c.id, request)
    db.commit()
    return {"deleted": len(hidden), "unread": _unread(db)}


@router.get("/{conv_id}")
def get_conversation(conv_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    c = _get(db, conv_id)
    mailbox = db.get(EmailAccount, c.email_account_id)
    contact = db.get(Contact, c.contact_id) if c.contact_id else None
    campaign = db.get(Campaign, c.campaign_id) if c.campaign_id else None
    messages = []
    for s in db.scalars(select(SentMessage).where(SentMessage.conversation_id == c.id)):
        messages.append({"id": str(s.id), "direction": "out", "kind": s.kind, "from_email": s.from_email,
                         "from_name": mailbox.display_name if mailbox else None, "to": s.to_email, "subject": s.subject,
                         "html": s.body_html, "text": None, "at": s.sent_at.isoformat()})
    for r in db.scalars(select(ReceivedMessage).where(ReceivedMessage.conversation_id == c.id)):
        messages.append({"id": str(r.id), "direction": "in", "kind": r.kind, "from_email": r.from_email,
                         "from_name": r.from_name, "to": r.to_email, "subject": r.subject, "html": r.body_html,
                         "text": r.body_text, "intent": r.intent, "at": r.received_at.isoformat()})
    messages.sort(key=lambda m: m["at"])
    return {
        "id": str(c.id), "subject": c.subject, "is_read": c.is_read, "intent": c.intent,
        "mailbox": {"id": str(mailbox.id), "email": mailbox.email, "provider": mailbox.provider,
                    "status": mailbox.status} if mailbox else None,
        "contact": {"id": str(contact.id), "email": contact.email, "first_name": contact.first_name,
                    "last_name": contact.last_name, "company_name": contact.company_name,
                    "lead_status": contact.lead_status, "phone": contact.phone} if contact else None,
        "campaign": {"id": str(campaign.id), "name": campaign.name} if campaign else None,
        "messages": messages,
    }


@router.patch("/{conv_id}")
def update_conversation(conv_id: uuid.UUID, body: ConversationPatch, user: User = Depends(writer),
                        db: Session = Depends(get_db)):
    c = _get(db, conv_id)
    data = body.model_dump(exclude_unset=True)
    if "is_read" in data and data["is_read"] is not None:
        c.is_read = data["is_read"]
    if "intent" in data:
        old, c.intent = c.intent, data["intent"]
        if c.contact_id and old != c.intent:
            log_activity(db, c.contact_id, "reply_labelled", f"Reply labelled {c.intent or 'unlabelled'}", user)
    db.commit()
    return {"id": str(c.id), "is_read": c.is_read, "intent": c.intent}


@router.post("/{conv_id}/reply")
def reply(conv_id: uuid.UUID, body: ReplyIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    c = _get(db, conv_id)
    mailbox = db.get(EmailAccount, c.email_account_id)
    if mailbox is None or mailbox.status not in ("connected", "paused"):
        raise HTTPException(409, "This conversation's mailbox is not connected")
    last_in = db.scalar(select(ReceivedMessage).where(ReceivedMessage.conversation_id == c.id,
                                                      ReceivedMessage.kind != "bounce")
                        .order_by(ReceivedMessage.received_at.desc()).limit(1))
    if last_in is None:
        raise HTTPException(409, "Nothing to reply to in this conversation")
    refs = [r for r in db.scalars(select(SentMessage.rfc_message_id).where(SentMessage.conversation_id == c.id)
                                  .order_by(SentMessage.sent_at))]
    if last_in.rfc_message_id:
        refs.append(last_in.rfc_message_id)
    subject = c.subject or last_in.subject or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    text = body.body.strip()
    if mailbox.signature_html:
        html = text_to_html(text) + f"<br>{mailbox.signature_html}"
    else:
        html = text_to_html(text)
    rfc_id = new_message_id(mailbox.email)
    out = OutgoingMessage(from_email=mailbox.email, from_name=mailbox.display_name, to_email=last_in.from_email,
                          subject=subject, text=text, html=html, rfc_message_id=rfc_id,
                          in_reply_to=last_in.rfc_message_id, references=refs[-10:],
                          provider_thread_id=c.provider_thread_id)
    try:
        result = get_provider(mailbox, "reply").send(out)
    except ProviderUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, f"Reply could not be sent: {exc}") from exc
    now = utcnow()
    db.add(SentMessage(email_account_id=mailbox.id, conversation_id=c.id, campaign_id=c.campaign_id,
                       contact_id=c.contact_id, sent_by_user_id=user.id, kind="manual_reply",
                       to_email=last_in.from_email, from_email=mailbox.email, subject=subject, body_html=html,
                       rfc_message_id=rfc_id, provider_message_id=result.provider_message_id,
                       provider_thread_id=result.provider_thread_id or c.provider_thread_id, sent_at=now))
    c.last_message_at, c.is_read = now, True
    if c.contact_id:
        log_activity(db, c.contact_id, "email_sent", f"Replied: {text[:300]}", user, conversation_id=str(c.id))
    audit(db, user, "inbox.reply", "conversation", c.id, request)
    db.commit()
    return {"message": "Reply sent" + (" (sandbox: written to the local outbox)" if mailbox.provider == "sandbox" else "")}
