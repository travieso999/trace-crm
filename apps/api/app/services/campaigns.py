"""Campaign state machine, enrollment, sequence management and stop/cancel primitives."""

import uuid
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
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

# enrollment states that still expect emails; paused prospects resume where they left off
LIVE = ("active", "paused")

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
        if s.position == 1 and s.status == "draft":
            problems.append("The first email is still a draft: activate it before starting")
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
    campaign.paused_at = utcnow()
    # Release queued (never-sent) work so nothing is sent while paused; next_send_at is preserved.
    db.execute(delete(ScheduledMessage).where(ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status == "queued"))


def resume(db: Session, campaign: Campaign) -> None:
    if campaign.status not in ("paused", "error"):
        raise CampaignError(f"Cannot resume a {campaign.status} campaign")
    problems = preflight(db, campaign)
    if problems:
        raise CampaignError("Campaign is not ready to send", problems)
    now = utcnow()
    if campaign.status == "paused" and campaign.paused_at:
        # follow-ups keep their spacing: everything still pending moves later by the length of the pause, so
        # resuming does not fire a backlog of emails that came due while paused
        db.execute(
            update(CampaignContact)
            .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active",
                   CampaignContact.next_send_at.is_not(None))
            .values(next_send_at=func.greatest(CampaignContact.next_send_at + (now - campaign.paused_at), now))
        )
    transition(campaign, "active")
    campaign.last_error = None
    campaign.paused_at = None
    _activate(db, campaign, now)


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
        .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status.in_(LIVE))
        .values(status="stopped", stop_reason="campaign_stopped", next_send_at=None)
    )


def maybe_complete(db: Session, campaign: Campaign) -> bool:
    if campaign.status != "active":
        return False
    remaining = db.scalar(
        select(func.count()).select_from(CampaignContact).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.status.in_(LIVE)
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


def enroll_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID], user: User | None,
                    *, reactivate_removed: bool = True) -> dict:
    """Add prospects. Skips the suppressed, invalid emails, closed stages and prospects already in the campaign.
    A prospect removed by hand comes back only when added explicitly (not by a dynamic audience) and continues
    after the last step they received, so no step is ever sent twice."""
    if campaign.status in ("completed", "stopped") or campaign.archived_at:
        raise CampaignError("Cannot add prospects to a finished or archived campaign")
    ids = list(dict.fromkeys(contact_ids))
    if not ids:
        return {"enrolled": 0, "skipped_existing": 0, "skipped_suppressed": 0, "skipped_invalid": 0}
    contacts = list(db.scalars(select(Contact).where(Contact.id.in_(ids), Contact.deleted_at.is_(None))))
    rows = {cc.contact_id: cc for cc in db.scalars(select(CampaignContact).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id.in_(ids)))}
    existing = {cid for cid, cc in rows.items() if not (reactivate_removed and cc.status == "removed")}
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
        elif c.id in rows:  # removed earlier, added back on purpose
            cc = rows[c.id]
            cc.status, cc.stop_reason, cc.paused_at = "active", None, None
            # not started yet: waits for the campaign start; otherwise the next follow-up is due now
            cc.next_send_at = now if campaign.status == "active" or cc.current_step else None
            log_activity(db, c.id, "campaign", f"Added back to campaign “{campaign.name}”", user,
                         campaign_id=str(campaign.id))
            result["enrolled"] += 1
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
        select(CampaignContact).where(CampaignContact.contact_id == contact_id, CampaignContact.status.in_(LIVE))
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
    if cc.status not in LIVE:
        return False
    cc.status, cc.stop_reason, cc.next_send_at = "replied", "replied", None
    _cancel_queued(db, [cc.id], "contact replied")
    return True


def remove_contact(db: Session, campaign: Campaign, contact_id: uuid.UUID) -> bool:
    cc = db.scalar(select(CampaignContact).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id == contact_id).with_for_update())
    if not cc:
        return False
    if cc.status != "removed":
        # the row and all its email history stay; a reply or bounce keeps its record on the prospect
        was = cc.status
        cc.status, cc.next_send_at = "removed", None
        cc.stop_reason = "manually_removed" if was in LIVE else f"manually_removed (was {was})"
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
        step.name = (data.get("name") or "").strip()[:120] or None
        step.status = data.get("status") or "active"
        step.send_at = data.get("send_at")
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
        campaign_type=campaign.campaign_type, target_location=campaign.target_location, industry=campaign.industry,
        created_by=user.id if user else None,
    )
    copy.mailboxes = list(campaign.mailboxes)
    db.add(copy)
    db.flush()
    for s in campaign.steps:
        db.add(SequenceStep(campaign_id=copy.id, position=s.position, delay_days=s.delay_days,
                            business_days=s.business_days, subject=s.subject, body=s.body,
                            reply_in_thread=s.reply_in_thread, name=s.name, status=s.status))
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
    # replies by reply time, so removing a prospect afterwards does not erase their reply from the numbers
    for cid, n in db.execute(
        select(CampaignContact.campaign_id, func.count()).where(
            CampaignContact.campaign_id.in_(campaign_ids), CampaignContact.replied_at.is_not(None))
        .group_by(CampaignContact.campaign_id)
    ):
        out[cid]["replied"] = n
    for v in out.values():
        v["reply_rate"] = round(100 * v["replied"] / v["sent"], 1) if v["sent"] else 0.0
    return out


# --- per-prospect controls ------------------------------------------------------------------------------------

def _ccs(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID], statuses: tuple[str, ...]) -> list[CampaignContact]:
    return list(db.scalars(select(CampaignContact).where(
        CampaignContact.campaign_id == campaign.id, CampaignContact.contact_id.in_(contact_ids),
        CampaignContact.status.in_(statuses)).with_for_update()))


def pause_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID]) -> int:
    ccs = _ccs(db, campaign, contact_ids, ("active",))
    now = utcnow()
    for cc in ccs:
        cc.status, cc.paused_at = "paused", now
    _cancel_queued(db, [cc.id for cc in ccs], "prospect paused")
    return len(ccs)


def resume_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID]) -> int:
    """Continue where each prospect left off; pending follow-ups move later by how long they were paused."""
    ccs = _ccs(db, campaign, contact_ids, ("paused",))
    now = utcnow()
    for cc in ccs:
        if cc.next_send_at is not None and cc.paused_at is not None:
            cc.next_send_at = max(cc.next_send_at + (now - cc.paused_at), now)
        elif cc.next_send_at is None and cc.current_step == 0 and campaign.status == "active":
            cc.next_send_at = now
        cc.status, cc.paused_at = "active", None
    return len(ccs)


def stop_contacts(db: Session, campaign: Campaign, contact_ids: list[uuid.UUID]) -> int:
    """No more emails from this campaign for these prospects (they stay in the campaign and in Prospects)."""
    ccs = _ccs(db, campaign, contact_ids, LIVE)
    for cc in ccs:
        cc.status, cc.stop_reason, cc.next_send_at, cc.paused_at = "stopped", "stopped_by_user", None, None
    _cancel_queued(db, [cc.id for cc in ccs], "follow-ups stopped by user")
    return len(ccs)


def copy_contacts(db: Session, source: Campaign, target: Campaign, contact_ids: list[uuid.UUID], user: User | None,
                  *, move: bool) -> dict:
    """Add the prospects to another campaign (its own sequence starts from its first email). Moving also removes
    them from this campaign; nothing they already received is lost."""
    if source.id == target.id:
        raise CampaignError("Pick a different campaign")
    in_source = list(db.scalars(select(CampaignContact.contact_id).where(
        CampaignContact.campaign_id == source.id, CampaignContact.contact_id.in_(contact_ids))))
    result = enroll_contacts(db, target, in_source, user)
    if move:
        for cid in in_source:
            remove_contact(db, source, cid)
        result["removed_from_source"] = len(in_source)
    return result


# --- sending a step now, and who a step goes to ------------------------------------------------------------

def _waiting_for(campaign: Campaign, step: SequenceStep):
    """Prospects whose next email is this step: they received the previous one and have not replied or stopped."""
    return (CampaignContact.campaign_id == campaign.id, CampaignContact.status == "active",
            CampaignContact.current_step == step.position - 1)


def send_step_now(db: Session, campaign: Campaign, step: SequenceStep) -> int:
    """Send this step to everyone currently waiting for it, without waiting for its delay or date. The usual
    rules still apply at sending time: suppression, unsubscribes, replies, sending window and daily limits."""
    if campaign.status != "active":
        raise CampaignError("Start or resume the campaign first")
    if step.status != "active":
        raise CampaignError("This email is a draft or paused: activate it first")
    now = utcnow()
    if step.send_at and step.send_at > now:
        step.send_at = None
    db.execute(update(CampaignContact).where(*_waiting_for(campaign, step), or_(
        CampaignContact.next_send_at.is_(None), CampaignContact.next_send_at > now)).values(next_send_at=now))
    return db.scalar(select(func.count()).select_from(CampaignContact).where(*_waiting_for(campaign, step))) or 0


def step_audience(db: Session, campaign: Campaign, step: SequenceStep, sample: int = 25) -> dict:
    """Who this step will go to, who already got it, and who will not get it and why."""
    sent = db.scalar(select(func.count(func.distinct(ScheduledMessage.campaign_contact_id))).where(
        ScheduledMessage.sequence_step_id == step.id, ScheduledMessage.status == "sent")) or 0
    waiting = db.scalar(select(func.count()).select_from(CampaignContact).where(*_waiting_for(campaign, step))) or 0
    # reached the previous step (or never started, for step 1) but will not continue
    excluded = dict(db.execute(
        select(CampaignContact.status, func.count()).where(
            CampaignContact.campaign_id == campaign.id, CampaignContact.current_step == step.position - 1,
            CampaignContact.status != "active")
        .group_by(CampaignContact.status)).all())
    rows = db.execute(
        select(Contact.id, Contact.email, Contact.first_name, Contact.last_name, Contact.company_name,
               CampaignContact.next_send_at)
        .join(CampaignContact, CampaignContact.contact_id == Contact.id)
        .where(*_waiting_for(campaign, step)).order_by(CampaignContact.next_send_at.nulls_last(), Contact.email)
        .limit(sample)).all()
    return {
        "step_id": str(step.id), "position": step.position, "already_sent": sent, "waiting": waiting,
        "excluded": {k: excluded.get(k, 0) for k in ("replied", "unsubscribed", "bounced", "paused", "stopped",
                                                       "removed", "completed", "error")},
        "sample": [{"contact_id": str(r.id), "email": r.email, "first_name": r.first_name, "last_name": r.last_name,
                    "company_name": r.company_name,
                    "next_send_at": r.next_send_at.isoformat() if r.next_send_at else None} for r in rows],
    }


def recipient_summary(db: Session, campaign: Campaign) -> dict:
    """The breakdown shown before sending: who is in the campaign and who will actually be emailed."""
    from app.services.enrollment import missing_variables

    rows = db.execute(
        select(CampaignContact.status, CampaignContact.replied_at, Contact.email, Contact.verification_status, Contact)
        .join(Contact, Contact.id == CampaignContact.contact_id)
        .where(CampaignContact.campaign_id == campaign.id, CampaignContact.status != "removed",
               Contact.deleted_at.is_(None))).all()
    emails = [r.email.lower() for r in rows]
    suppression = dict(db.execute(select(SuppressionEntry.email, SuppressionEntry.reason)
                                  .where(SuppressionEntry.email.in_(emails))).all()) if emails else {}
    seen: set[str] = set()
    out = {"total": len(rows), "valid_emails": 0, "invalid_emails": 0, "duplicate_addresses": 0,
           "unsubscribed": 0, "suppressed": 0, "replied": 0, "paused": 0, "finished_or_stopped": 0,
           "missing_data": 0, "missing_variables": {}, "eligible": 0}
    for r in rows:
        email = r.email.lower()
        dup = email in seen
        seen.add(email)
        if dup:
            out["duplicate_addresses"] += 1
        if r.verification_status == "invalid":
            out["invalid_emails"] += 1
        else:
            out["valid_emails"] += 1
        reason = suppression.get(email)
        if r.status == "unsubscribed" or reason == "unsubscribe":
            out["unsubscribed"] += 1
        elif r.status == "bounced" or reason:
            out["suppressed"] += 1
        elif r.replied_at is not None or r.status == "replied":
            out["replied"] += 1
        elif r.status == "paused":
            out["paused"] += 1
        elif r.status != "active":
            out["finished_or_stopped"] += 1
        elif r.verification_status != "invalid" and not dup:
            # the sender blocks a prospect whose merge fields are empty (unless the campaign uses fallbacks)
            missing = missing_variables(db, campaign, r.Contact)
            if missing:
                out["missing_data"] += 1
                for name in missing:
                    out["missing_variables"][name] = out["missing_variables"].get(name, 0) + 1
            else:
                out["eligible"] += 1
    return out


# --- dynamic audiences ---------------------------------------------------------------------------------------

def sync_dynamic_audiences(db: Session, limit: int = 1000) -> int:
    """Add newly matching prospects to running campaigns with a dynamic audience. Prospects removed by hand are
    never added back automatically (they are still in the campaign, as removed)."""
    from app.services.audience import AudienceFilter, contacts_query

    added = 0
    campaigns = list(db.scalars(select(Campaign).where(
        Campaign.audience_mode == "dynamic", Campaign.status.in_(("active", "scheduled")),
        Campaign.deleted_at.is_(None), Campaign.archived_at.is_(None))))
    for c in campaigns:
        f = AudienceFilter.model_validate(c.audience_filter or {})
        if f.is_empty():
            continue
        f = f.model_copy(update={"not_in_campaign_id": c.id})
        ids = list(db.scalars(contacts_query(f).with_only_columns(Contact.id).order_by(Contact.created_at).limit(limit)))
        if ids:
            added += enroll_contacts(db, c, ids, None, reactivate_removed=False)["enrolled"]
    return added
