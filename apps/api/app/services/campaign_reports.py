"""Campaign email history and analytics. Read-only.

Only what the mail system actually reports is counted. SMTP and the Gmail API confirm that a message was accepted
for delivery, and bounces and replies arrive later as email; nothing reports opens or delivery to the inbox, so
those are not shown. "Delivered" is therefore "sent and not bounced (so far)"."""

import uuid

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    EmailEvent,
    ReceivedMessage,
    ScheduledMessage,
    SentMessage,
)


def _per_step_events(db: Session, campaign_id: uuid.UUID) -> dict[uuid.UUID, dict[str, int]]:
    """Bounces, replies and unsubscribes attributed to the step whose email they answered."""
    out: dict[uuid.UUID, dict[str, int]] = {}
    rows = db.execute(
        select(ScheduledMessage.sequence_step_id, EmailEvent.type, func.count(func.distinct(EmailEvent.contact_id)))
        .join(SentMessage, SentMessage.id == EmailEvent.sent_message_id)
        .join(ScheduledMessage, ScheduledMessage.id == SentMessage.scheduled_message_id)
        .where(EmailEvent.campaign_id == campaign_id,
               EmailEvent.type.in_(("bounced", "replied", "unsubscribed", "positive_reply")))
        .group_by(ScheduledMessage.sequence_step_id, EmailEvent.type))
    for step_id, typ, n in rows:
        out.setdefault(step_id, {})[typ] = n
    return out


def steps_report(db: Session, campaign: Campaign) -> list[dict]:
    status_counts: dict[uuid.UUID, dict[str, int]] = {}
    for step_id, st, n in db.execute(
        select(ScheduledMessage.sequence_step_id, ScheduledMessage.status, func.count())
        .where(ScheduledMessage.campaign_id == campaign.id)
        .group_by(ScheduledMessage.sequence_step_id, ScheduledMessage.status)):
        status_counts.setdefault(step_id, {})[st] = n
    sent_times = {sid: (first, last) for sid, first, last in db.execute(
        select(ScheduledMessage.sequence_step_id, func.min(SentMessage.sent_at), func.max(SentMessage.sent_at))
        .join(SentMessage, SentMessage.scheduled_message_id == ScheduledMessage.id)
        .where(ScheduledMessage.campaign_id == campaign.id).group_by(ScheduledMessage.sequence_step_id))}
    events = _per_step_events(db, campaign.id)
    out = []
    for s in campaign.steps:
        waiting = db.scalar(select(func.count()).select_from(CampaignContact).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active",
            CampaignContact.current_step == s.position - 1)) or 0
        next_at = db.scalar(select(func.min(CampaignContact.next_send_at)).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active",
            CampaignContact.current_step == s.position - 1))
        if s.send_at and (next_at is None or s.send_at > next_at):
            next_at = s.send_at
        sc = status_counts.get(s.id, {})
        ev = events.get(s.id, {})
        sent = sc.get("sent", 0)
        first, last = sent_times.get(s.id, (None, None))
        if s.status == "draft":
            state = "draft"
        elif s.status == "paused":
            state = "paused"
        elif sent and not waiting:
            state = "sent"
        elif sent:
            state = "sending"
        elif waiting or sc.get("queued"):
            state = "scheduled"
        else:
            state = "waiting"  # nobody has reached this step yet
        out.append({
            "step_id": str(s.id), "position": s.position, "name": s.name, "subject": s.subject, "body": s.body,
            "status": s.status, "state": state, "delay_days": s.delay_days, "business_days": s.business_days,
            "send_at": s.send_at.isoformat() if s.send_at else None,
            "next_send_at": next_at.isoformat() if next_at else None,
            "first_sent_at": first.isoformat() if first else None, "last_sent_at": last.isoformat() if last else None,
            "recipients": sent + sc.get("queued", 0) + sc.get("sending", 0) + sc.get("unknown", 0) + sc.get("failed", 0),
            "waiting": waiting, "queued": sc.get("queued", 0) + sc.get("sending", 0) + sc.get("unknown", 0),
            "sent": sent, "failed": sc.get("failed", 0), "bounced": ev.get("bounced", 0),
            "delivered": max(sent - ev.get("bounced", 0), 0), "replied": ev.get("replied", 0),
            "positive": ev.get("positive_reply", 0), "unsubscribed": ev.get("unsubscribed", 0),
        })
    return out


def step_messages(db: Session, campaign: Campaign, step_id: uuid.UUID, limit: int = 500) -> list[dict]:
    """Every prospect this step was sent (or tried) to, with what happened."""
    rows = db.execute(
        select(ScheduledMessage.status, ScheduledMessage.last_error, ScheduledMessage.due_at, SentMessage.sent_at,
               SentMessage.subject, SentMessage.id, Contact.id, Contact.email, Contact.first_name, Contact.last_name,
               Contact.company_name, CampaignContact.status, CampaignContact.replied_at)
        .join(CampaignContact, CampaignContact.id == ScheduledMessage.campaign_contact_id)
        .join(Contact, Contact.id == CampaignContact.contact_id)
        .outerjoin(SentMessage, SentMessage.scheduled_message_id == ScheduledMessage.id)
        .where(ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.sequence_step_id == step_id)
        .order_by(SentMessage.sent_at.desc().nulls_first(), Contact.email).limit(limit)).all()
    bounced = set(db.scalars(select(EmailEvent.sent_message_id).where(
        EmailEvent.campaign_id == campaign.id, EmailEvent.type == "bounced", EmailEvent.sent_message_id.is_not(None))))
    return [{
        "contact_id": str(r[6]), "email": r[7], "first_name": r[8], "last_name": r[9], "company_name": r[10],
        "status": "bounced" if r[5] in bounced else r[0], "error": r[1],
        "due_at": r[2].isoformat() if r[2] else None, "sent_at": r[3].isoformat() if r[3] else None,
        "subject": r[4], "campaign_status": r[11], "replied_at": r[12].isoformat() if r[12] else None,
    } for r in rows]


def contact_history(db: Session, campaign: Campaign, contact_id: uuid.UUID) -> list[dict]:
    """Every email to and from one prospect in this campaign, oldest first. Nothing is ever deleted."""
    sent = db.execute(
        select(SentMessage, ScheduledMessage.sequence_step_id)
        .outerjoin(ScheduledMessage, ScheduledMessage.id == SentMessage.scheduled_message_id)
        .where(SentMessage.campaign_id == campaign.id, SentMessage.contact_id == contact_id)).all()
    positions = {s.id: s.position for s in campaign.steps}
    items = [{
        "direction": "out", "kind": m.kind, "at": m.sent_at.isoformat(), "subject": m.subject, "body_html": m.body_html,
        "from_email": m.from_email, "to_email": m.to_email, "step": positions.get(step_id),
    } for m, step_id in sent]
    conv_ids = {m.conversation_id for m, _ in sent if m.conversation_id}
    if conv_ids:
        for r in db.scalars(select(ReceivedMessage).where(ReceivedMessage.conversation_id.in_(conv_ids))):
            items.append({"direction": "in", "kind": r.kind, "at": r.received_at.isoformat(), "subject": r.subject,
                          "body_text": r.body_text, "from_email": r.from_email, "intent": r.intent, "step": None})
    pending = db.execute(
        select(ScheduledMessage.status, ScheduledMessage.due_at, ScheduledMessage.last_error,
               ScheduledMessage.sequence_step_id)
        .join(CampaignContact, CampaignContact.id == ScheduledMessage.campaign_contact_id)
        .where(ScheduledMessage.campaign_id == campaign.id, CampaignContact.contact_id == contact_id,
               ScheduledMessage.status.in_(("queued", "failed", "cancelled")))).all()
    for st, due, err, step_id in pending:
        items.append({"direction": "pending" if st == "queued" else st, "kind": "sequence",
                      "at": due.isoformat() if due else None, "subject": None, "error": err,
                      "step": positions.get(step_id)})
    items.sort(key=lambda i: i["at"] or "")
    return items


def analytics(db: Session, campaign: Campaign) -> dict:
    cid = campaign.id
    by_status = dict(db.execute(select(CampaignContact.status, func.count())
                                .where(CampaignContact.campaign_id == cid)
                                .group_by(CampaignContact.status)).all())
    total = sum(n for st, n in by_status.items() if st != "removed")
    sm = dict(db.execute(select(ScheduledMessage.status, func.count()).where(ScheduledMessage.campaign_id == cid)
                         .group_by(ScheduledMessage.status)).all())
    sent = sm.get("sent", 0)
    first_pos = {s.id for s in campaign.steps if s.position == 1}
    followups_sent = db.scalar(select(func.count()).select_from(ScheduledMessage).where(
        ScheduledMessage.campaign_id == cid, ScheduledMessage.status == "sent",
        ScheduledMessage.sequence_step_id.not_in(first_pos) if first_pos else true())) or 0
    emailed = db.scalar(select(func.count(func.distinct(SentMessage.contact_id))).where(
        SentMessage.campaign_id == cid, SentMessage.kind == "sequence")) or 0

    def distinct_contacts(event_type: str) -> int:
        return db.scalar(select(func.count(func.distinct(EmailEvent.contact_id))).where(
            EmailEvent.campaign_id == cid, EmailEvent.type == event_type)) or 0

    bounced = db.scalar(select(func.count(func.distinct(EmailEvent.sent_message_id))).where(
        EmailEvent.campaign_id == cid, EmailEvent.type == "bounced")) or 0
    replied = db.scalar(select(func.count()).select_from(CampaignContact).where(
        CampaignContact.campaign_id == cid, CampaignContact.replied_at.is_not(None))) or 0
    positive = distinct_contacts("positive_reply")
    # unsubscribed by link or reply (events) or by being added to the suppression list as an unsubscribe
    unsubscribed = len(set(db.scalars(select(EmailEvent.contact_id).where(
        EmailEvent.campaign_id == cid, EmailEvent.type == "unsubscribed", EmailEvent.contact_id.is_not(None))))
        | set(db.scalars(select(CampaignContact.contact_id).where(
            CampaignContact.campaign_id == cid, CampaignContact.status == "unsubscribed"))))
    n_steps = len(campaign.steps)
    awaiting = db.scalar(select(func.count()).select_from(CampaignContact).where(
        CampaignContact.campaign_id == cid, CampaignContact.status == "active",
        CampaignContact.current_step >= 1, CampaignContact.current_step < n_steps)) or 0
    not_started = db.scalar(select(func.count()).select_from(CampaignContact).where(
        CampaignContact.campaign_id == cid, CampaignContact.status == "active",
        CampaignContact.current_step == 0)) or 0

    def pct(n: int, d: int) -> float | None:
        return round(100 * n / d, 1) if d else None

    return {
        "total_contacts": total, "contacts_emailed": emailed, "emails_sent": sent, "failed": sm.get("failed", 0),
        "queued": sm.get("queued", 0) + sm.get("sending", 0), "bounced": bounced,
        "delivered": max(sent - bounced, 0), "replies": replied, "positive_replies": positive,
        "unsubscribes": unsubscribed, "followups_sent": followups_sent, "awaiting_followup": awaiting,
        "not_started": not_started, "paused": by_status.get("paused", 0),
        "completed_sequence": by_status.get("completed", 0), "removed": by_status.get("removed", 0),
        "rates": {
            "delivery_rate": pct(sent - bounced, sent), "bounce_rate": pct(bounced, sent),
            "reply_rate": pct(replied, emailed), "positive_reply_rate": pct(positive, emailed),
            "unsubscribe_rate": pct(unsubscribed, emailed),
        },
        "definitions": {
            "emails_sent": "Emails the mail server accepted for delivery (initial emails and follow-ups)",
            "delivered": "Emails sent minus bounces reported so far. Inbox placement is not reported by any provider",
            "delivery_rate": "(emails sent − bounced) ÷ emails sent",
            "bounce_rate": "bounced emails ÷ emails sent",
            "reply_rate": "prospects who replied ÷ prospects who were emailed at least once",
            "positive_reply_rate": "prospects with a reply classified positive ÷ prospects emailed",
            "unsubscribe_rate": "prospects who unsubscribed ÷ prospects emailed",
            "opens": "Not tracked: this CRM does not add tracking pixels",
        },
    }
