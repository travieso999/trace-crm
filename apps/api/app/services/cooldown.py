"""Cross-campaign cooldown: one prospect never gets cold emails from two campaigns close together.

A campaign's ``cross_campaign_cooldown_days`` (0 = off) makes each of its emails wait until that many days have
passed since another campaign emailed the prospect. It is enforced at send reservation (``sending._reserve``), under
a per-prospect lock, so two campaigns reaching the same prospect at the same moment cannot both pass the check.
The same lookups feed the send dialog and the enrollment preview, so the wait is visible before anything is sent.

Only sequence emails count: test emails and manual replies in a conversation never start a cooldown.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignContact, ScheduledMessage, SentMessage

DEFAULT_DAYS = 14  # the column's default; also the window the send dialog reports when the cooldown is off
IN_FLIGHT_RECHECK = timedelta(minutes=15)
RUNNING = ("active", "scheduled")


@dataclass
class RecentSend:
    sent_at: datetime
    campaign_id: uuid.UUID | None
    campaign_name: str | None

    def until(self, days: int) -> datetime:
        return self.sent_at + timedelta(days=days)


def lock_contact(db: Session, contact_id: uuid.UUID) -> None:
    """Serialize cooldown checks for one prospect until the transaction ends (reservations of other campaigns wait)."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"cooldown:{contact_id}", 0))))


def recent_other_sends(db: Session, campaign_id: uuid.UUID, contact_ids: list[uuid.UUID], since: datetime
                       ) -> dict[uuid.UUID, RecentSend]:
    """Per prospect, the latest sequence email from any *other* campaign sent at or after ``since``."""
    if not contact_ids:
        return {}
    rows = db.execute(
        select(SentMessage.contact_id, SentMessage.sent_at, SentMessage.campaign_id, Campaign.name)
        .outerjoin(Campaign, Campaign.id == SentMessage.campaign_id)
        .where(SentMessage.contact_id.in_(contact_ids), SentMessage.kind == "sequence",
               SentMessage.campaign_id.is_not(None), SentMessage.campaign_id != campaign_id,
               SentMessage.sent_at >= since)
        .order_by(SentMessage.contact_id, SentMessage.sent_at.desc())
        .distinct(SentMessage.contact_id)
    ).all()
    return {r.contact_id: RecentSend(r.sent_at, r.campaign_id, r.name) for r in rows}


def other_send_in_flight(db: Session, campaign_id: uuid.UUID, contact_id: uuid.UUID) -> bool:
    """Another campaign's email to this prospect is being sent right now (or its outcome is being reconciled)."""
    return db.scalar(
        select(func.count()).select_from(ScheduledMessage)
        .join(CampaignContact, CampaignContact.id == ScheduledMessage.campaign_contact_id)
        .where(CampaignContact.contact_id == contact_id, ScheduledMessage.campaign_id != campaign_id,
               ScheduledMessage.status.in_(("sending", "unknown")))
    ) > 0


def other_running_campaigns(db: Session, campaign_id: uuid.UUID, contact_ids: list[uuid.UUID]
                            ) -> dict[uuid.UUID, str]:
    """Prospects still being emailed by another running campaign: contact id -> that campaign's name."""
    if not contact_ids:
        return {}
    rows = db.execute(
        select(CampaignContact.contact_id, Campaign.name)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .where(CampaignContact.contact_id.in_(contact_ids), CampaignContact.campaign_id != campaign_id,
               CampaignContact.status == "active", Campaign.status.in_(RUNNING), Campaign.deleted_at.is_(None))
        .order_by(CampaignContact.contact_id, Campaign.created_at)
    ).all()
    out: dict[uuid.UUID, str] = {}
    for cid, name in rows:
        out.setdefault(cid, name)
    return out


def describe(send: RecentSend, *, capital: bool = False) -> str:
    name = f"“{send.campaign_name}”" if send.campaign_name else "another campaign"
    return f"{'E' if capital else 'e'}mailed by {name} on {send.sent_at:%b} {send.sent_at.day}"
