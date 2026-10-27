"""Campaign state machine, enrollment, sequence management and stop/cancel primitives."""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AiPersonalization,
    Campaign,
    CampaignContact,
    Contact,
    EmailEvent,
    ScheduledMessage,
    SequenceStep,
    SuppressionEntry,
    User,
)
from app.models.base import utcnow
from app.providers import send_block_reason
from app.services import app_settings
from app.services.contacts import log_activity, template_variables
from app.services.rendering import MissingVariables, render_string

# Allowed transitions: current -> {target: action name}
TRANSITIONS: dict[str, set[str]] = {
    "draft": {"scheduled", "active"},
    "scheduled": {"active", "paused", "stopped", "draft"},
    "active": {"paused", "stopped", "completed", "error"},
    "paused": {"active", "stopped"},
    "error": {"active", "paused", "stopped"},
    "completed": set(),
    "stopped": set(),
}


class CampaignError(ValueError):
    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = problems or [message]


def transition(campaign: Campaign, target: str) -> None:
    if target not in TRANSITIONS.get(campaign.status, set()):
        raise CampaignError(f"Cannot move campaign from {campaign.status} to {target}")
    campaign.status = target


def preflight(db: Session, campaign: Campaign) -> list[str]:
    """Return blocking problems that prevent a campaign from sending."""
    problems = list(app_settings.compliance_ready(db))
    steps = campaign.steps
    if not steps:
        problems.append("Add at least one sequence step")
    for s in steps:
        if not s.body.strip():
            problems.append(f"Step {s.position} has an empty body")
        if s.position == 1 and not s.subject.strip():
            problems.append("Step 1 needs a subject line")
        if s.position > 1 and not s.reply_in_thread and not s.subject.strip():
            problems.append(f"Step {s.position} is sent as a new thread and needs a subject")
    connected = [m for m in campaign.mailboxes if m.status == "connected"]
    usable = [m for m in connected if send_block_reason(m, "campaign") is None]
    if not connected:
        problems.append("Assign at least one connected email account")
    elif not usable:
        problems.append(send_block_reason(connected[0], "campaign") + " — campaigns can only use sandbox mailboxes")
    elif get_settings().real_sending == "test_only" and any(m.provider != "sandbox" for m in usable):
        allow = get_settings().test_recipient_allowlist or {""}  # empty allowlist: every lead is outside it
        outside = db.scalar(
            select(func.count()).select_from(CampaignContact).join(Contact, Contact.id == CampaignContact.contact_id)
            .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active",
                   func.lower(Contact.email).not_in(allow))
        ) or 0
        if outside:
            problems.append(
                f"Test-only mode (REAL_SENDING=test_only): {outside} enrolled prospect(s) are not in "
                "ALLOWED_TEST_RECIPIENTS. A real mailbox can only send this campaign to allow-listed test addresses"
            )
    active = db.scalar(
        select(func.count()).select_from(CampaignContact).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active"
        )
    )
    if not active:
        problems.append("Enroll at least one eligible prospect")
    if campaign.require_approved_personalization and active:
        missing = db.scalar(
            select(func.count()).select_from(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status == "active",
                ~CampaignContact.contact_id.in_(
                    select(AiPersonalization.contact_id).where(AiPersonalization.status == "approved")
                ),
            )
        )
        if missing:
            problems.append(f"{missing} enrolled prospect(s) have no approved personalization")
    return problems


def variable_report(db: Session, campaign: Campaign, limit: int = 2000) -> dict:
    """How many active leads would be blocked by missing template variables."""
    blocked: dict[str, int] = {}
    count = 0
    ccs = db.scalars(
        select(CampaignContact).where(CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active").limit(limit)
    )
    for cc in ccs:
        vars_ = template_variables(cc.contact)
        missing: set[str] = set()
        for s in campaign.steps:
            for tpl in (s.subject, s.body):
                try:
                    render_string(tpl, vars_, policy=campaign.missing_variable_policy, fallbacks=campaign.variable_fallbacks)
                except MissingVariables as exc:
                    missing.update(exc.names)
        if missing:
            count += 1
            for n in missing:
                blocked[n] = blocked.get(n, 0) + 1
    return {"leads_blocked": count, "by_variable": blocked}


def start(db: Session, campaign: Campaign, user: User | None) -> None:
    problems = preflight(db, campaign)
    if problems:
        raise CampaignError("Campaign is not ready to send", problems)
    now = utcnow()
    target = "scheduled" if campaign.start_at and campaign.start_at > now else "active"
    transition(campaign, target)
    campaign.last_error = None
    if target == "active":
        _activate(db, campaign, now)


def _activate(db: Session, campaign: Campaign, now: datetime) -> None:
    campaign.started_at = campaign.started_at or now
    db.execute(
        update(CampaignContact)
        .where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status == "active",
            CampaignContact.next_send_at.is_(None),
            CampaignContact.current_step == 0,
        )
        .values(next_send_at=now)
    )


def activate_scheduled(db: Session) -> int:
    """Beat task helper: move scheduled campaigns whose start time has arrived to active."""
    now = utcnow()
    n = 0
    for c in db.scalars(select(Campaign).where(Campaign.status == "scheduled", Campaign.start_at <= now).with_for_update()):
        if preflight(db, c):
            c.status, c.last_error = "error", "; ".join(preflight(db, c))
            continue
        transition(c, "active")
        _activate(db, c, now)
        n += 1
    return n


def pause(db: Session, campaign: Campaign) -> None:
    transition(campaign, "paused")
    # Release queued (never-sent) work so nothing is sent while paused; next_send_at is preserved.
    db.execute(delete(ScheduledMessage).where(ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status == "queued"))


def resume(db: Session, campaign: Campaign) -> None:
    if campaign.status not in ("paused", "error"):
        raise CampaignError(f"Cannot resume a {campaign.status} campaign")
    problems = preflight(db, campaign)
    if problems:
        raise CampaignError("Campaign is not ready to send", problems)
    transition(campaign, "active")
    campaign.last_error = None
    _activate(db, campaign, utcnow())


def stop(db: Session, campaign: Campaign) -> None:
    transition(campaign, "stopped")
    campaign.completed_at = utcnow()
    db.execute(
        update(ScheduledMessage)
        .where(ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status == "queued")
        .values(status="cancelled", last_error="campaign stopped")
    )
    db.execute(
        update(CampaignContact)
        .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active")
        .values(status="stopped", stop_reason="campaign_stopped", next_send_at=None)
    )


def maybe_complete(db: Session, campaign: Campaign) -> bool:
    if campaign.status != "active":
        return False
    remaining = db.scalar(
        select(func.count()).select_from(CampaignContact).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active"
        )
    )
    pending = db.scalar(
        select(func.count()).select_from(ScheduledMessage).where(
            ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status.in_(("queued", "sending", "unknown"))
        )
    )
    if remaining == 0 and pending == 0:
        transition(campaign, "completed")
        campaign.completed_at = utcnow()
        return True
    return False


def enroll_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID], user: User | None) -> dict:
    if campaign.status in ("completed", "stopped") or campaign.archived_at:
        raise CampaignError("Cannot add prospects to a finished or archived campaign")
    ids = list(dict.fromkeys(contact_ids))
    if not ids:
        return {"enrolled": 0, "skipped_existing": 0, "skipped_suppressed": 0, "skipped_invalid": 0}
    contacts = list(db.scalars(select(Contact).where(Contact.id.in_(ids), Contact.deleted_at.is_(None))))
    existing = set(db.scalars(select(CampaignContact.contact_id).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id.in_(ids))))
    suppressed = set(db.scalars(select(SuppressionEntry.email).where(SuppressionEntry.email.in_([c.email for c in contacts]))))
    now = utcnow()
    result = {"enrolled": 0, "skipped_existing": 0, "skipped_suppressed": 0, "skipped_invalid": 0}
    for c in contacts:
        if c.id in existing:
            result["skipped_existing"] += 1
        elif c.email in suppressed:
            result["skipped_suppressed"] += 1
        elif c.verification_status == "invalid" or c.lead_status in ("not_interested", "won", "lost"):
            result["skipped_invalid"] += 1
        else:
            db.add(CampaignContact(
                campaign_id=campaign.id, contact_id=c.id, status="active",
                next_send_at=now if campaign.status == "active" else None,
            ))
            log_activity(db, c.id, "campaign", f"Added to campaign “{campaign.name}”", user, campaign_id=str(campaign.id))
            result["enrolled"] += 1
    db.flush()
    return result


def _cancel_queued(db: Session, cc_ids: list[uuid.UUID], reason: str) -> None:
    if cc_ids:
        db.execute(
            update(ScheduledMessage)
            .where(ScheduledMessage.campaign_contact_id.in_(cc_ids), ScheduledMessage.status == "queued")
            .values(status="cancelled", last_error=reason)
        )


def stop_contact_everywhere(db: Session, contact_id: uuid.UUID, reason: str, status: str = "stopped") -> int:
    """Stop every active enrollment of a contact. Row locks serialise with the sender."""
    ccs = list(db.scalars(
        select(CampaignContact).where(CampaignContact.contact_id == contact_id, CampaignContact.status == "active")
        .with_for_update()
    ))
    for cc in ccs:
        cc.status, cc.stop_reason, cc.next_send_at = status, reason, None
    _cancel_queued(db, [cc.id for cc in ccs], reason)
    return len(ccs)


def stop_email_everywhere(db: Session, email: str, reason: str, status: str) -> int:
    ids = list(db.scalars(select(Contact.id).where(Contact.email == email.lower())))
    return sum(stop_contact_everywhere(db, cid, reason, status) for cid in ids)


def mark_replied(db: Session, cc_id: uuid.UUID, at: datetime) -> bool:
    cc = db.scalar(select(CampaignContact).where(CampaignContact.id == cc_id).with_for_update())
    if cc is None:
        return False
    cc.replied_at = cc.replied_at or at
    if cc.status != "active":
        return False
    cc.status, cc.stop_reason, cc.next_send_at = "replied", "replied", None
    _cancel_queued(db, [cc.id], "contact replied")
    return True


def remove_contact(db: Session, campaign: Campaign, contact_id: uuid.UUID) -> bool:
    cc = db.scalar(select(CampaignContact).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id == contact_id).with_for_update())
    if not cc:
        return False
    if cc.status == "active":
        cc.status, cc.stop_reason, cc.next_send_at = "removed", "manually_removed", None
        _cancel_queued(db, [cc.id], "manually removed")
    return True


def replace_steps(db: Session, campaign: Campaign, steps: list[dict]) -> None:
    """Replace the sequence. Steps keep their id when supplied so sent history stays linked."""
    if campaign.status in ("completed", "stopped"):
        raise CampaignError("Cannot edit the sequence of a finished campaign")
    if len(steps) > 20:
        raise CampaignError("A sequence can have at most 20 steps")
    existing = {s.id: s for s in campaign.steps}
    sent_step_ids = set(db.scalars(select(ScheduledMessage.sequence_step_id).where(
        ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status.in_(("sent", "sending", "unknown")))))
    keep_ids = {s.get("id") for s in steps if s.get("id")}
    removed_sent = [sid for sid in existing if sid not in keep_ids and sid in sent_step_ids]
    if removed_sent:
        raise CampaignError("Steps that already sent messages cannot be deleted (edit them instead)")
    # move existing positions out of the way to satisfy the unique (campaign, position) constraint
    for s in existing.values():
        s.position = -s.position - 1000
    db.flush()
    new_list = []
    for i, data in enumerate(steps, start=1):
        step = existing.get(data.get("id")) if data.get("id") else None
        if step is None:
            step = SequenceStep(campaign_id=campaign.id)
            db.add(step)
        step.position = i
        step.delay_days = 0 if i == 1 else int(data.get("delay_days", 0))
        step.business_days = bool(data.get("business_days", True))
        step.subject = data.get("subject", "")
        step.body = data.get("body", "")
        step.reply_in_thread = bool(data.get("reply_in_thread", True)) if i > 1 else False
        new_list.append(step)
    for sid, s in existing.items():
        if sid not in keep_ids:
            db.delete(s)
    db.flush()
    db.expire(campaign, ["steps"])


def duplicate(db: Session, campaign: Campaign, user: User | None) -> Campaign:
    copy = Campaign(
        name=f"{campaign.name} (copy)"[:200],
        description=campaign.description,
        status="draft",
        daily_limit=campaign.daily_limit,
        schedule=dict(campaign.schedule),
        missing_variable_policy=campaign.missing_variable_policy,
        variable_fallbacks=dict(campaign.variable_fallbacks or {}),
        require_approved_personalization=campaign.require_approved_personalization,
        stop_on_auto_reply=campaign.stop_on_auto_reply,
        created_by=user.id if user else None,
    )
    copy.mailboxes = list(campaign.mailboxes)
    db.add(copy)
    db.flush()
    for s in campaign.steps:
        db.add(SequenceStep(campaign_id=copy.id, position=s.position, delay_days=s.delay_days,
                            business_days=s.business_days, subject=s.subject, body=s.body,
                            reply_in_thread=s.reply_in_thread))
    db.flush()
    return copy


def stats(db: Session, campaign_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    out: dict[uuid.UUID, dict] = {cid: {"leads": 0, "active": 0, "replied": 0, "completed": 0, "bounced": 0,
                                         "unsubscribed": 0, "sent": 0, "queued": 0, "positive": 0}
                                  for cid in campaign_ids}
    if not campaign_ids:
        return out
    for cid, st, n in db.execute(
        select(CampaignContact.campaign_id, CampaignContact.status, func.count())
        .where(CampaignContact.campaign_id.in_(campaign_ids)).group_by(CampaignContact.campaign_id, CampaignContact.status)
    ):
        out[cid]["leads"] += n
        if st in out[cid]:
            out[cid][st] += n
    for cid, st, n in db.execute(
        select(ScheduledMessage.campaign_id, ScheduledMessage.status, func.count())
        .where(ScheduledMessage.campaign_id.in_(campaign_ids), ScheduledMessage.status.in_(("sent", "queued")))
        .group_by(ScheduledMessage.campaign_id, ScheduledMessage.status)
    ):
        out[cid][st] = n
    for cid, n in db.execute(
        select(EmailEvent.campaign_id, func.count()).where(
            EmailEvent.campaign_id.in_(campaign_ids), EmailEvent.type == "positive_reply")
        .group_by(EmailEvent.campaign_id)
    ):
        out[cid]["positive"] = n
    for v in out.values():
        v["reply_rate"] = round(100 * v["replied"] / v["sent"], 1) if v["sent"] else 0.0
    return out
