"""Reply sync and processing (ARCHITECTURE.md §5).

parse raw MIME → classify (bounce / auto-reply / unsubscribe request / human reply) → match to a sent
message and campaign-contact → effects under a row lock (stop the sequence, suppress, update CRM).
"""

import email
import logging
import re
import uuid
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

import nh3
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.logging import log
from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    EmailAccount,
    EmailEvent,
    ReceivedMessage,
    SentMessage,
    SuppressionEntry,
)
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.base import InboundRaw, ProviderError
from app.services import app_settings
from app.services import campaigns as campaign_service
from app.services.contacts import log_activity, set_lead_status
from app.services.sending import get_or_create_conversation

logger = logging.getLogger("app.inbound")

MSGID = re.compile(r"<[^<>\s]+>")
UNSUBSCRIBE_PHRASES = (
    "unsubscribe", "remove me", "take me off", "stop emailing", "stop contacting", "do not contact",
    "don't contact", "dont contact", "opt out", "opt-out",
)
POSITIVE_PHRASES = (
    "interested", "sounds good", "let's talk", "lets talk", "let's chat", "happy to chat", "set up a call",
    "schedule a call", "book a call", "send me more", "tell me more", "what times", "what does your", "pricing",
    "how much", "yes please",
)
NEGATIVE_PHRASES = (
    "not interested", "no thanks", "no thank you", "not a good fit", "we're all set", "we are all set",
    "not at this time", "no need", "please don't", "not looking",
)
AUTO_SUBJECTS = ("automatic reply", "auto reply", "auto-reply", "out of office", "out of the office", "autoreply",
                 "away from", "vacation")
BOUNCE_SENDERS = ("mailer-daemon", "postmaster")


def campaign_mail():
    """Sent messages that make a thread a campaign conversation: sequence steps, and inbox replies within a campaign
    conversation. Test sends and replies to non-campaign mail don't count."""
    return or_(SentMessage.kind == "sequence",
               and_(SentMessage.kind == "manual_reply", SentMessage.campaign_id.is_not(None)))
STAGE_ORDER = ["new_lead", "contacted", "replied", "interested", "meeting_scheduled", "proposal_sent", "won"]


# --- parsing --------------------------------------------------------------------------------------

def _body(m: EmailMessage) -> tuple[str, str | None]:
    text_part = m.get_body(preferencelist=("plain",))
    html_part = m.get_body(preferencelist=("html",))
    text = text_part.get_content() if text_part else ""
    html = html_part.get_content() if html_part else None
    if not text and html:
        text = re.sub(r"<[^>]+>", " ", html)
    return text.strip(), html


def latest_text(text: str) -> str:
    """The new part of a reply: drop quoted history ("On ... wrote:", "> ..." lines)."""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(">") or re.match(r"^On .+ wrote:$", s) or s.startswith("-----Original Message-----"):
            break
        if re.match(r"^From: .+", s) and lines:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def parse(raw: bytes) -> dict:
    m: EmailMessage = email.message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]
    from_name, from_email = parseaddr(m.get("From", ""))
    refs = MSGID.findall(m.get("References", "") or "") + MSGID.findall(m.get("In-Reply-To", "") or "")
    try:
        received_at = parsedate_to_datetime(m["Date"]) if m.get("Date") else None
    except (TypeError, ValueError):
        received_at = None
    text, html = _body(m)
    dsn_recipients = []
    if m.get_content_type() == "multipart/report":
        dsn_recipients = re.findall(r"Final-Recipient:\s*rfc822;\s*([^\s]+)", raw.decode("utf-8", "replace"), re.I)
    return {
        "from_email": (from_email or "").lower(),
        "from_name": from_name or None,
        "to": ", ".join(a for _, a in getaddresses(m.get_all("To", []))) or None,
        "subject": (m.get("Subject") or "").strip() or None,
        "message_id": (MSGID.findall(m.get("Message-ID", "") or "") or [None])[0],
        "in_reply_to": (MSGID.findall(m.get("In-Reply-To", "") or "") or [None])[0],
        "references": list(dict.fromkeys(refs)),
        "auto_submitted": (m.get("Auto-Submitted") or "").lower(),
        "x_autoreply": bool(m.get("X-Autoreply") or m.get("X-Autorespond")),
        "precedence": (m.get("Precedence") or "").lower(),
        "content_type": m.get_content_type(),
        "text": text,
        "html": html,
        "received_at": received_at,
        "dsn_recipients": [r.strip("<>").lower() for r in dsn_recipients],
    }


def classify(p: dict) -> str:
    sender = p["from_email"].split("@", 1)[0]
    subject = (p["subject"] or "").lower()
    if p["content_type"] == "multipart/report" or sender in BOUNCE_SENDERS or subject.startswith(
            ("undeliverable", "delivery status notification", "mail delivery failed", "returned mail")):
        return "bounce"
    if (p["auto_submitted"] and p["auto_submitted"] != "no") or p["x_autoreply"] or \
            p["precedence"] in ("auto_reply", "bulk", "junk") or subject.startswith(AUTO_SUBJECTS):
        return "auto_reply"
    new_text = latest_text(p["text"]).lower()
    if any(ph in new_text for ph in UNSUBSCRIBE_PHRASES) and len(new_text) < 400:
        return "unsubscribe"
    return "reply"


def intent_of(text: str) -> tuple[str, float]:
    """Keyword heuristic until AI classification is configured. Negative phrases win over positive ones."""
    t = latest_text(text).lower()
    if any(ph in t for ph in NEGATIVE_PHRASES):
        return "negative", 0.7
    if any(ph in t for ph in POSITIVE_PHRASES):
        return "positive", 0.6
    return "neutral", 0.4


# --- matching -------------------------------------------------------------------------------------

def match(db: Session, account: EmailAccount, item: InboundRaw, p: dict) -> SentMessage | None:
    """The campaign email this message answers (provider thread → In-Reply-To/References → bounce recipient), or None.
    Only campaign mail counts, not test sends, and the sender's address alone never makes a match: personal mail
    from someone who is also a lead is not a campaign reply."""
    latest = (SentMessage.sent_at.desc(),)
    if item.provider_thread_id:
        sent = db.scalar(select(SentMessage).where(
            campaign_mail(), SentMessage.email_account_id == account.id,
            SentMessage.provider_thread_id == item.provider_thread_id).order_by(*latest).limit(1))
        if sent:
            return sent
    if p["references"]:
        sent = db.scalar(select(SentMessage).where(campaign_mail(), SentMessage.rfc_message_id.in_(p["references"]))
                         .order_by(*latest).limit(1))
        if sent:
            return sent
    if p["dsn_recipients"]:
        return db.scalar(select(SentMessage).where(
            campaign_mail(), SentMessage.email_account_id == account.id,
            SentMessage.to_email.in_(p["dsn_recipients"])).order_by(*latest).limit(1))
    return None


def suppress(db: Session, email_addr: str, reason: str, source: str) -> None:
    if not db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == email_addr)):
        db.add(SuppressionEntry(email=email_addr, reason=reason, source=source))
        db.flush()


def _advance_stage(db: Session, contact: Contact, target: str) -> None:
    """Move forward through the pipeline only; never demote a lead that is further along."""
    cur = contact.lead_status
    if cur in ("lost", "not_interested", "won"):
        return
    if cur in STAGE_ORDER and target in STAGE_ORDER and STAGE_ORDER.index(target) <= STAGE_ORDER.index(cur):
        return
    set_lead_status(db, contact, target, None, reason="reply")


# --- processing -----------------------------------------------------------------------------------

def _not_a_campaign_reply(db: Session, p: dict, kind: str) -> None:
    """Mail that answers no campaign email is not stored and never reaches the inbox. When the sender is a lead
    writing in directly, an unsubscribe request is still honoured and their active sequences still stop, so no
    follow-up goes to someone who asked to be removed or is already talking to us."""
    if kind not in ("reply", "unsubscribe") or not p["from_email"]:
        return
    contact = db.scalar(select(Contact).where(Contact.email == p["from_email"], Contact.deleted_at.is_(None)))
    if contact is None:
        return
    if kind == "unsubscribe" and app_settings.get_section(db, "compliance").get("suppress_on_unsubscribe_reply", True):
        new = not db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == contact.email))
        suppress(db, contact.email, "reply_request", "unsubscribe email (outside a campaign thread)")
        stopped = campaign_service.stop_contact_everywhere(db, contact.id, "unsubscribe_reply", "unsubscribed")
        if new or stopped:
            log_activity(db, contact.id, "unsubscribe", "Asked to be removed (direct email, not a campaign reply)")
    elif campaign_service.stop_contact_everywhere(db, contact.id, "replied", "replied"):
        log_activity(db, contact.id, "reply", "Emailed directly outside a campaign thread: active sequences stopped")
    db.flush()


def process(db: Session, account: EmailAccount, item: InboundRaw) -> ReceivedMessage | None:
    """Store and act on one inbound message. Idempotent per (mailbox, provider message id)."""
    if db.scalar(select(ReceivedMessage.id).where(
            ReceivedMessage.email_account_id == account.id, ReceivedMessage.provider_message_id == item.provider_message_id)):
        return None
    p = parse(item.raw)
    if p["from_email"] == account.email.lower():
        return None  # our own message echoed back by the provider
    kind = classify(p)
    sent = match(db, account, item, p)
    if sent is None:
        _not_a_campaign_reply(db, p, kind)
        return None
    contact = db.get(Contact, sent.contact_id) if sent.contact_id else None
    now = utcnow()
    # Provider receive time (or now) orders the thread; the Date header is sender-controlled and often skewed.
    at = item.received_at or now
    thread_id = item.provider_thread_id or (sent.provider_thread_id if sent else None) or f"in-{item.provider_message_id}"
    conv = get_or_create_conversation(db, account.id, thread_id, contact_id=contact.id if contact else None,
                                      campaign_id=sent.campaign_id if sent else None, subject=p["subject"])
    intent, confidence = (intent_of(p["text"]) if kind == "reply" else (None, None))
    msg = ReceivedMessage(
        email_account_id=account.id, conversation_id=conv.id, contact_id=contact.id if contact else None,
        provider_message_id=item.provider_message_id, provider_thread_id=thread_id, rfc_message_id=p["message_id"],
        in_reply_to=p["in_reply_to"], from_email=p["from_email"], from_name=p["from_name"], to_email=p["to"],
        subject=p["subject"], body_text=p["text"][:100_000],
        body_html=nh3.clean(p["html"])[:200_000] if p["html"] else None,
        kind=kind if (contact or kind == "bounce") else "unmatched", intent=intent, intent_confidence=confidence,
        received_at=at,
    )
    db.add(msg)
    try:
        db.flush()
    except IntegrityError:  # concurrent sync of the same message
        db.rollback()
        return None
    if kind != "bounce":
        conv.has_inbound, conv.is_read = True, False
        conv.last_message_at = max(conv.last_message_at or at, at)
        if intent:
            conv.intent = intent

    campaign_id = sent.campaign_id if sent else None
    cc = None
    if contact and campaign_id:
        cc = db.scalar(select(CampaignContact).where(
            CampaignContact.campaign_id == campaign_id, CampaignContact.contact_id == contact.id))
    event = {"contact_id": contact.id if contact else None, "campaign_id": campaign_id, "email_account_id": account.id,
             "sent_message_id": sent.id if sent else None, "occurred_at": at}

    if kind == "bounce":
        target = (p["dsn_recipients"] or [sent.to_email if sent else None])[0]
        if target:
            suppress(db, target, "bounce", f"bounce via {account.email}")
            campaign_service.stop_email_everywhere(db, target, "bounced", "bounced")
            bounced = contact or db.scalar(select(Contact).where(Contact.email == target, Contact.deleted_at.is_(None)))
            if bounced:
                bounced.verification_status = "invalid"
                log_activity(db, bounced.id, "bounce", f"Email to {target} bounced")
                event["contact_id"] = bounced.id
        db.add(EmailEvent(type="bounced", data={"recipient": target}, **event))
    elif contact is None:
        pass  # unmatched: stays in the inbox for a human, no automation
    elif kind == "unsubscribe":
        if app_settings.get_section(db, "compliance").get("suppress_on_unsubscribe_reply", True):
            suppress(db, contact.email, "reply_request", "unsubscribe reply")
            campaign_service.stop_contact_everywhere(db, contact.id, "unsubscribe_reply", "unsubscribed")
        if cc:
            campaign_service.mark_replied(db, cc.id, at)
        set_lead_status(db, contact, "not_interested", None, reason="unsubscribe_reply")
        log_activity(db, contact.id, "unsubscribe", "Asked to be removed (reply)")
        db.add(EmailEvent(type="unsubscribed", data={"via": "reply"}, **event))
    elif kind == "auto_reply":
        db.add(EmailEvent(type="auto_reply", data={}, **event))
        campaign = db.get(Campaign, campaign_id) if campaign_id else None
        if cc and campaign and campaign.stop_on_auto_reply:
            campaign_service.mark_replied(db, cc.id, at)
        log_activity(db, contact.id, "auto_reply", f"Auto-reply: {p['subject'] or ''}".strip())
    else:
        if cc:
            campaign_service.mark_replied(db, cc.id, at)
        else:  # reply outside a known thread still stops every active sequence for this person
            campaign_service.stop_contact_everywhere(db, contact.id, "replied", "replied")
        contact.last_replied_at = max(contact.last_replied_at or at, at)
        db.add(EmailEvent(type="replied", data={}, **event))
        if intent == "positive":
            db.add(EmailEvent(type="positive_reply", data={"confidence": confidence}, **event))
            _advance_stage(db, contact, "interested")
        elif intent == "negative":
            db.add(EmailEvent(type="negative_reply", data={"confidence": confidence}, **event))
            set_lead_status(db, contact, "not_interested", None, reason="negative_reply")
        else:
            _advance_stage(db, contact, "replied")
        log_activity(db, contact.id, "reply", (latest_text(p["text"]) or p["subject"] or "")[:500],
                     conversation_id=str(conv.id), intent=intent)
    db.flush()
    return msg


def current_error(account: EmailAccount) -> str | None:
    """The mailbox's last error, unless a later successful sync superseded it (an authorization error stands until
    the mailbox is reconnected). Keeps a resolved failure from showing as current, whichever process recorded it."""
    if not account.last_error:
        return None
    if account.status != "auth_error" and account.last_error_at and account.last_synced_at \
            and account.last_synced_at > account.last_error_at:
        return None
    return account.last_error


def sync_mailbox(account_id: uuid.UUID) -> dict:
    """Pull and process new inbound mail for one mailbox. The cursor only advances after a commit."""
    with SessionLocal() as db:
        account = db.get(EmailAccount, account_id)
        if account is None or account.status in ("disconnected", "paused"):
            return {"processed": 0, "skipped": True}
        try:
            items, cursor = get_provider(account).fetch_new(account.sync_cursor)
        except ProviderError as exc:
            account.last_error, account.last_error_at = str(exc)[:2000], utcnow()
            db.commit()
            return {"processed": 0, "error": str(exc)}
        processed = 0
        for item in items:
            if process(db, account, item) is not None:
                processed += 1
        account.sync_cursor, account.last_synced_at = cursor, utcnow()
        # a successful sync supersedes an earlier sync failure; auth errors keep their own status until reconnect
        if account.last_error and account.status != "auth_error":
            account.last_error = account.last_error_at = None
        db.commit()
    if processed:
        log(logger, logging.INFO, "mailbox_synced", mailbox_id=str(account_id), processed=processed)
    return {"processed": processed}


def build_simulated_reply(sent: SentMessage, body: str, *, kind: str = "reply", from_name: str | None = None,
                          at: datetime | None = None) -> bytes:
    """Dev helper: an inbound MIME message answering ``sent`` (reply, auto-reply or bounce DSN)."""
    from email.utils import formatdate, make_msgid

    if kind == "bounce":  # RFC 3464 DSN, written out literally (the stdlib generator can't build message/delivery-status)
        domain = sent.from_email.rsplit("@", 1)[-1]
        boundary = make_msgid().strip("<>").replace("@", "_")
        return (
            f"From: Mail Delivery Subsystem <mailer-daemon@{domain}>\r\nTo: {sent.from_email}\r\n"
            f"Subject: Delivery Status Notification (Failure)\r\nDate: {formatdate(usegmt=True)}\r\n"
            f"Message-ID: {make_msgid(domain=domain)}\r\nIn-Reply-To: {sent.rfc_message_id}\r\n"
            f"MIME-Version: 1.0\r\nContent-Type: multipart/report; report-type=delivery-status; boundary=\"{boundary}\"\r\n"
            f"\r\n--{boundary}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            f"{body or f'Delivery to {sent.to_email} failed permanently.'}\r\n"
            f"--{boundary}\r\nContent-Type: message/delivery-status\r\n\r\nReporting-MTA: dns; sandbox\r\n\r\n"
            f"Final-Recipient: rfc822; {sent.to_email}\r\nAction: failed\r\nStatus: 5.1.1\r\n"
            f"--{boundary}--\r\n"
        ).encode()
    m = EmailMessage()
    m["From"] = f"{from_name} <{sent.to_email}>" if from_name else sent.to_email
    subject = sent.subject if sent.subject.lower().startswith("re:") else f"Re: {sent.subject}"
    m["Subject"] = f"Automatic reply: {sent.subject}" if kind == "auto_reply" else subject
    if kind == "auto_reply":
        m["Auto-Submitted"] = "auto-replied"
    m.set_content(f"{body}\n\nOn {formatdate(usegmt=True)}, {sent.from_email} wrote:\n> (original message)")
    m["To"] = sent.from_email
    m["Date"] = formatdate(at.timestamp() if at else None, usegmt=True)
    m["Message-ID"] = make_msgid(domain=sent.to_email.rsplit("@", 1)[-1])
    m["In-Reply-To"] = sent.rfc_message_id
    m["References"] = sent.rfc_message_id
    return m.as_bytes()
