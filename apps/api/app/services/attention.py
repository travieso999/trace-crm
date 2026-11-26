"""What needs a person: one list for the Overview page's "Needs attention" box and the daily summary email.

Each item says what is wrong, how many, and where to fix it. Includes a self-check for prospects that should have been
emailed but have nothing queued ("stuck"), so a bug like that shows up on day one instead of sitting unnoticed.
"""

from datetime import timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import Campaign, CampaignContact, Contact, EmailAccount, EmailEvent, ScheduledMessage, SequenceStep
from app.models.base import utcnow
from app.services.audience import no_company_name, no_first_name

# due this long ago with nothing queued counts as stuck (the engine runs at least hourly, so this is never just late)
STUCK_AFTER = timedelta(hours=24)
RECENT = timedelta(days=7)


def items(db: Session) -> list[dict]:
    now = utcnow()
    out: list[dict] = []
    live_campaign = and_(Campaign.deleted_at.is_(None), Campaign.archived_at.is_(None))

    waiting = db.scalar(
        select(func.count(func.distinct(CampaignContact.contact_id)))
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .join(Contact, Contact.id == CampaignContact.contact_id)
        .where(CampaignContact.status == "removed", CampaignContact.stop_reason == "missing_data", live_campaign,
               Contact.deleted_at.is_(None), or_(no_first_name(), no_company_name()))) or 0
    if waiting:
        out.append(_item("missing_data", "warning", f"{waiting} prospect{_s(waiting)} waiting for a name or company",
                         "Left out of their campaign until it's filled in; they go back in automatically.",
                         "/lists?tab=missing", waiting))

    for a in db.scalars(select(EmailAccount).where(EmailAccount.status.in_(("auth_error", "error")))
                        .order_by(EmailAccount.email)):
        out.append(_item("mailbox", "error", f"Mailbox {a.email} can't send",
                         "Sign-in expired: reconnect it." if a.status == "auth_error" else (a.last_error or "Sending error")[:200],
                         "/email-accounts", 1))

    stuck = _stuck(db, now)
    if stuck:
        names = ", ".join(sorted({n for n, _ in stuck}))[:200]
        out.append(_item("stuck", "error", f"{len(stuck)} prospect{_s(len(stuck))} overdue with nothing queued",
                         f"Should have been emailed over a day ago ({names}). This is a fault: tell your developer.",
                         f"/campaigns/{stuck[0][1]}", len(stuck)))

    for name, cid, n in db.execute(
            select(Campaign.name, Campaign.id, func.count()).join(CampaignContact, CampaignContact.campaign_id == Campaign.id)
            .where(CampaignContact.status == "paused", live_campaign, Campaign.status.in_(("active", "paused", "scheduled")))
            .group_by(Campaign.name, Campaign.id).order_by(Campaign.name)):
        out.append(_item("paused", "info", f"{n} paused prospect{_s(n)} in {name}",
                         "They get nothing until you resume them.", f"/campaigns/{cid}", n))

    for name, cid, err in db.execute(select(Campaign.name, Campaign.id, Campaign.last_error).where(
            live_campaign, Campaign.status.in_(("active", "error")), Campaign.last_error.is_not(None))):
        out.append(_item("campaign_error", "error", f"{name} has a problem", err[:200], f"/campaigns/{cid}", 1))

    bounced = db.scalar(select(func.count()).select_from(EmailEvent).where(
        EmailEvent.type == "bounced", EmailEvent.occurred_at >= now - RECENT)) or 0
    if bounced:
        out.append(_item("bounced", "info", f"{bounced} bounce{_s(bounced)} in the last 7 days",
                         "Those addresses were stopped and suppressed automatically.", "/suppressions", bounced))

    failed = db.scalar(select(func.count()).select_from(ScheduledMessage).where(
        ScheduledMessage.status == "failed", ScheduledMessage.updated_at >= now - RECENT)) or 0
    if failed:
        out.append(_item("failed", "warning", f"{failed} email{_s(failed)} failed to send in the last 7 days",
                         "The mail provider refused them; see the campaign's Send queue.", "/campaigns", failed))
    return out


def _stuck(db: Session, now) -> list[tuple[str, str]]:
    """Active prospects in a running campaign, due over a day ago, whose next email can go out (it exists, is active
    and its date has come) but nothing is queued or sending for them."""
    nxt = aliased(SequenceStep)
    pending = exists().where(ScheduledMessage.campaign_contact_id == CampaignContact.id,
                             ScheduledMessage.status.in_(("queued", "sending", "unknown")))
    return [(name, str(cid)) for name, cid in db.execute(
        select(Campaign.name, Campaign.id)
        .select_from(CampaignContact)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .join(nxt, and_(nxt.campaign_id == CampaignContact.campaign_id, nxt.position == CampaignContact.current_step + 1))
        .where(CampaignContact.status == "active", CampaignContact.next_send_at <= now - STUCK_AFTER,
               Campaign.status == "active", Campaign.deleted_at.is_(None), nxt.status == "active",
               or_(nxt.send_at.is_(None), nxt.send_at <= now - STUCK_AFTER), ~pending)
        .limit(500))]


def _item(kind: str, severity: str, title: str, detail: str, href: str, count: int) -> dict:
    return {"kind": kind, "severity": severity, "title": title, "detail": detail, "href": href, "count": count}


def _s(n: int) -> str:
    return "" if n == 1 else "s"
