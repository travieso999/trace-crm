"""Analytics computed from the email_events ledger and current state tables.

"accepted" = provider accepted the message. We never claim "delivered": neither Gmail nor Graph
confirms delivery to external recipients. Rates use accepted messages as the denominator.
"""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import Date, cast, func, select
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    CrmActivity,
    EmailAccount,
    EmailEvent,
    ScheduledMessage,
    SentMessage,
)
from app.models.base import utcnow
from app.models.crm import LEAD_STATUSES
from app.services import app_settings
from app.services.scheduling import _tz, start_of_local_day

EVENT_KEYS = ["attempted", "accepted", "failed", "bounced", "replied", "positive_reply", "negative_reply",
              "unsubscribed", "complaint", "meeting_booked", "auto_reply"]


def _rate(n: int, d: int) -> float:
    return round(100.0 * n / d, 2) if d else 0.0


def event_filters(start: datetime | None, end: datetime | None, campaign_id=None, mailbox_id=None, state=None, tag=None):
    conds = []
    if start:
        conds.append(EmailEvent.occurred_at >= start)
    if end:
        conds.append(EmailEvent.occurred_at < end)
    if campaign_id:
        conds.append(EmailEvent.campaign_id == campaign_id)
    if mailbox_id:
        conds.append(EmailEvent.email_account_id == mailbox_id)
    if state or tag:
        sub = select(Contact.id)
        if state:
            sub = sub.where(Contact.state == state.upper())
        if tag:
            from app.models import Tag, contact_tags

            sub = sub.where(Contact.id.in_(select(contact_tags.c.contact_id).join(Tag).where(Tag.name == tag)))
        conds.append(EmailEvent.contact_id.in_(sub))
    return conds


def summary(db: Session, conds: list) -> dict:
    counts = dict.fromkeys(EVENT_KEYS, 0)
    for t, n in db.execute(select(EmailEvent.type, func.count()).where(*conds).group_by(EmailEvent.type)):
        counts[t] = n
    accepted = counts["accepted"]
    return {
        **counts,
        "reply_rate": _rate(counts["replied"], accepted),
        "positive_reply_rate": _rate(counts["positive_reply"], accepted),
        "bounce_rate": _rate(counts["bounced"], accepted),
        "unsubscribe_rate": _rate(counts["unsubscribed"], accepted),
        "failure_rate": _rate(counts["failed"], counts["attempted"]),
    }


def daily_series(db: Session, conds: list, days: int, tz_name: str, types: tuple[str, ...]) -> list[dict]:
    tz = _tz(tz_name)
    local_day = cast(func.timezone(tz.key, EmailEvent.occurred_at), Date)
    rows = db.execute(
        select(local_day, EmailEvent.type, func.count()).where(*conds, EmailEvent.type.in_(types))
        .group_by(local_day, EmailEvent.type)
    )
    data: dict[date, dict] = {}
    for d, t, n in rows:
        data.setdefault(d, {})[t] = n
    today = utcnow().astimezone(tz).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        out.append({"date": d.isoformat(), **{t: data.get(d, {}).get(t, 0) for t in types}})
    return out


def funnel(db: Session) -> list[dict]:
    counts = dict(db.execute(
        select(Contact.lead_status, func.count()).where(Contact.deleted_at.is_(None)).group_by(Contact.lead_status)
    ).all())
    return [{"stage": s, "count": counts.get(s, 0)} for s in LEAD_STATUSES]


def per_campaign(db: Session, conds: list, limit: int = 20) -> list[dict]:
    rows = db.execute(
        select(EmailEvent.campaign_id, EmailEvent.type, func.count()).where(*conds, EmailEvent.campaign_id.is_not(None))
        .group_by(EmailEvent.campaign_id, EmailEvent.type)
    )
    data: dict[uuid.UUID, dict] = {}
    for cid, t, n in rows:
        data.setdefault(cid, dict.fromkeys(EVENT_KEYS, 0))[t] = n
    names = dict(db.execute(select(Campaign.id, Campaign.name).where(Campaign.id.in_(list(data)))).all()) if data else {}
    out = []
    for cid, c in data.items():
        out.append({"campaign_id": str(cid), "name": names.get(cid, "—"), **c,
                    "reply_rate": _rate(c["replied"], c["accepted"]), "bounce_rate": _rate(c["bounced"], c["accepted"])})
    out.sort(key=lambda r: r["accepted"], reverse=True)
    return out[:limit]


def per_mailbox(db: Session, conds: list) -> list[dict]:
    rows = db.execute(
        select(EmailEvent.email_account_id, EmailEvent.type, func.count())
        .where(*conds, EmailEvent.email_account_id.is_not(None)).group_by(EmailEvent.email_account_id, EmailEvent.type)
    )
    data: dict[uuid.UUID, dict] = {}
    for mid, t, n in rows:
        data.setdefault(mid, dict.fromkeys(EVENT_KEYS, 0))[t] = n
    emails = dict(db.execute(select(EmailAccount.id, EmailAccount.email).where(EmailAccount.id.in_(list(data)))).all()) if data else {}
    return [{"email_account_id": str(k), "email": emails.get(k, "—"), **v,
             "bounce_rate": _rate(v["bounced"], v["accepted"])} for k, v in data.items()]


def sent_count(db: Session, mailbox_id: uuid.UUID, since: datetime) -> int:
    return db.scalar(select(func.count()).select_from(SentMessage).where(
        SentMessage.email_account_id == mailbox_id, SentMessage.sent_at >= since)) or 0


def mailbox_health(db: Session) -> list[dict]:
    now = utcnow()
    out = []
    for m in db.scalars(select(EmailAccount).where(EmailAccount.status != "disconnected").order_by(EmailAccount.email)):
        since = start_of_local_day(now, (m.schedule or {}).get("timezone"))
        out.append({
            "id": str(m.id), "email": m.email, "provider": m.provider, "status": m.status,
            "sent_today": sent_count(db, m.id, since), "daily_limit": m.daily_limit,
            "last_error": m.last_error, "circuit_open": bool(m.circuit_open_until and m.circuit_open_until > now),
            "last_synced_at": m.last_synced_at.isoformat() if m.last_synced_at else None,
        })
    return out


def dashboard(db: Session) -> dict:
    tz_name = app_settings.get_section(db, "defaults").get("timezone") or "America/New_York"
    now = utcnow()
    day_start = start_of_local_day(now, tz_name)
    local_now = now.astimezone(_tz(tz_name))
    month_start = start_of_local_day(local_now.replace(day=1), tz_name)
    thirty = now - timedelta(days=30)

    def count_sent(since):
        return db.scalar(select(func.count()).select_from(SentMessage).where(
            SentMessage.sent_at >= since, SentMessage.kind != "test")) or 0

    totals = summary(db, [EmailEvent.occurred_at >= thirty])
    all_time = summary(db, [])
    upcoming = db.execute(
        select(CampaignContact.next_send_at, Contact.email, Contact.first_name, Contact.last_name, Campaign.name,
               CampaignContact.current_step)
        .join(Contact, Contact.id == CampaignContact.contact_id)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .where(CampaignContact.status == "active", CampaignContact.next_send_at.is_not(None), Campaign.status == "active")
        .order_by(CampaignContact.next_send_at).limit(10)
    ).all()
    activities = db.execute(
        select(CrmActivity, Contact.email, Contact.first_name, Contact.last_name)
        .join(Contact, Contact.id == CrmActivity.contact_id)
        .order_by(CrmActivity.created_at.desc()).limit(15)
    ).all()
    queued = db.scalar(select(func.count()).select_from(ScheduledMessage).where(ScheduledMessage.status == "queued"))
    return {
        "timezone": tz_name,
        "kpis": {
            "total_contacts": db.scalar(select(func.count()).select_from(Contact).where(Contact.deleted_at.is_(None))) or 0,
            "active_campaigns": db.scalar(select(func.count()).select_from(Campaign).where(
                Campaign.status == "active", Campaign.deleted_at.is_(None))) or 0,
            "sent_today": count_sent(day_start),
            "sent_this_month": count_sent(month_start),
            "replies_total": all_time["replied"],
            "positive_replies_total": all_time["positive_reply"],
            "meetings_booked": db.scalar(select(func.count()).select_from(Contact).where(
                Contact.deleted_at.is_(None), Contact.lead_status.in_(("meeting_scheduled", "proposal_sent", "won")))) or 0,
            "bounce_rate_30d": totals["bounce_rate"],
            "unsubscribe_rate_30d": totals["unsubscribe_rate"],
            "reply_rate_30d": totals["reply_rate"],
            "queued_messages": queued or 0,
        },
        "daily_volume": daily_series(db, [EmailEvent.occurred_at >= now - timedelta(days=31)], 30, tz_name,
                                     ("accepted", "failed", "bounced")),
        "reply_trend": daily_series(db, [EmailEvent.occurred_at >= now - timedelta(days=31)], 30, tz_name,
                                    ("replied", "positive_reply", "negative_reply")),
        "campaign_performance": per_campaign(db, [EmailEvent.occurred_at >= thirty], limit=8),
        "funnel": funnel(db),
        "mailbox_health": mailbox_health(db),
        "upcoming": [
            {"at": r[0].isoformat(), "email": r[1], "name": " ".join(p for p in (r[2], r[3]) if p), "campaign": r[4],
             "step": r[5] + 1} for r in upcoming
        ],
        "recent_activity": [
            {"id": str(a.id), "type": a.type, "summary": a.summary, "created_at": a.created_at.isoformat(),
             "contact_id": str(a.contact_id), "contact": " ".join(p for p in (fn, ln) if p) or email}
            for a, email, fn, ln in activities
        ],
    }
