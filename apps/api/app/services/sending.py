"""Sending engine (ARCHITECTURE.md §4): dispatch → reservation → provider send → result.

* Every phase runs in its own short transaction; the provider call happens with no transaction open.
* Row locks: scheduled message → campaign-contact → mailbox. Reply processing locks the same
  campaign-contact row, so a processed reply always wins over a pending follow-up.
* ``(campaign_contact_id, sequence_step_id)`` is unique: a step can never be queued twice.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.core.crypto import sign
from app.core.db import SessionLocal
from app.core.logging import log
from app.models import (
    AiPersonalization,
    Campaign,
    CampaignContact,
    Contact,
    Conversation,
    EmailAccount,
    EmailEvent,
    ScheduledMessage,
    SentMessage,
    SequenceStep,
    User,
)
from app.models.base import utcnow
from app.providers import get_provider, send_block_reason
from app.providers.base import (
    AmbiguousSendError,
    OutgoingMessage,
    PermanentProviderError,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailable,
    SendResult,
)
from app.providers.mime import new_message_id
from app.services import app_settings, video_assets
from app.services import campaigns as campaign_service
from app.services.contacts import is_suppressed, log_activity, set_lead_status, template_variables
from app.services.rendering import MissingVariables, Rendered, render_email, render_string
from app.services.scheduling import add_delay, next_joint_window, next_window_start, start_of_local_day

logger = logging.getLogger("app.sending")

BATCH = 200
MAX_ATTEMPTS = 5
CIRCUIT_THRESHOLD = 5
CIRCUIT_MINUTES = 30
RECONCILE_AFTER = timedelta(minutes=5)
# Large attachments make a send slow. A hosted pass is killed at 300 s, which would strand the message in flight, so
# stop starting new sends well before that (one more upload can still take up to 120 s); the rest stay queued.
SEND_BUDGET_SECONDS = 150


# --- rendering ------------------------------------------------------------------------------------

def unsubscribe_url(contact_id: uuid.UUID, campaign_id: uuid.UUID | None) -> str:
    token = sign(f"{contact_id}:{campaign_id or ''}", "unsubscribe")
    return f"{get_settings().public_url.rstrip('/')}/u/{token}"


def footer_text(db: Session, unsub_url: str) -> str:
    c = app_settings.get_section(db, "compliance")
    company = app_settings.get_section(db, "company")
    return render_string(c["footer_template"], {
        "company_name_sender": company.get("name") or "",
        "physical_address": c.get("physical_address") or "",
        "unsubscribe_url": unsub_url,
    })


TEST_RECIPIENT_ERROR = ("Test emails from a real mailbox can only go to addresses in ALLOWED_TEST_RECIPIENTS "
                        "(sandbox mailboxes may also use app users)")


def test_recipients(db: Session, mailbox: EmailAccount) -> set[str]:
    """Addresses a test email from ``mailbox`` may go to. A real mailbox reaches only ALLOWED_TEST_RECIPIENTS; a
    sandbox mailbox only writes to the local outbox, so app users are allowed there too."""
    allowed = get_settings().test_recipient_allowlist
    if mailbox.provider == "sandbox":
        allowed = allowed | set(db.scalars(select(User.email).where(User.is_active)))
    return allowed


def contact_variables(db: Session, contact: Contact) -> dict[str, str]:
    vars_ = template_variables(contact)
    approved = db.scalar(
        select(AiPersonalization.final_text).where(
            AiPersonalization.contact_id == contact.id, AiPersonalization.status == "approved")
        .order_by(AiPersonalization.approved_at.desc()).limit(1)
    )
    if approved:
        vars_["custom_intro"] = approved
    return vars_


def render_step(db: Session, campaign: Campaign, step: SequenceStep, cc: CampaignContact, contact: Contact) -> Rendered:
    """Render a step for one lead. Raises MissingVariables rather than sending a broken email."""
    unsub = unsubscribe_url(contact.id, campaign.id)
    threaded = step.position > 1 and step.reply_in_thread and cc.first_subject
    subject_tpl = step.subject
    if threaded:
        base = cc.first_subject or ""
        subject_tpl = base if base.lower().startswith("re:") else f"Re: {base}"
    rendered = render_email(
        subject_tpl if not threaded else "", step.body, contact_variables(db, contact),
        policy=campaign.missing_variable_policy, fallbacks=campaign.variable_fallbacks,
        footer=footer_text(db, unsub), video=video_assets.video_link(step.video_asset) if step.video_preview else None,
    )
    if threaded:
        rendered.subject = " ".join(subject_tpl.split())  # already-rendered subject of step 1
    return rendered


# --- dispatch -------------------------------------------------------------------------------------

def sent_today(db: Session, mailbox: EmailAccount, now: datetime) -> int:
    since = start_of_local_day(now, (mailbox.schedule or {}).get("timezone"))
    sent = db.scalar(select(func.count()).select_from(SentMessage).where(
        SentMessage.email_account_id == mailbox.id, SentMessage.sent_at >= since, SentMessage.kind != "test")) or 0
    in_flight = db.scalar(select(func.count()).select_from(ScheduledMessage).where(
        ScheduledMessage.email_account_id == mailbox.id, ScheduledMessage.status.in_(("sending", "unknown")),
        ScheduledMessage.reserved_at >= since)) or 0
    return sent + in_flight


def campaign_sent_today(db: Session, campaign: Campaign, now: datetime) -> int:
    since = start_of_local_day(now, (campaign.schedule or {}).get("timezone"))
    sent = db.scalar(select(func.count()).select_from(SentMessage).where(
        SentMessage.campaign_id == campaign.id, SentMessage.sent_at >= since, SentMessage.kind == "sequence")) or 0
    in_flight = db.scalar(select(func.count()).select_from(ScheduledMessage).where(
        ScheduledMessage.campaign_id == campaign.id, ScheduledMessage.status.in_(("sending", "unknown")),
        ScheduledMessage.reserved_at >= since)) or 0
    return sent + in_flight


def usable(mailbox: EmailAccount, now: datetime) -> bool:
    return (mailbox.status == "connected" and not (mailbox.circuit_open_until and mailbox.circuit_open_until > now)
            and send_block_reason(mailbox, "campaign") is None)


def pick_mailbox(db: Session, campaign: Campaign, now: datetime) -> uuid.UUID | None:
    """Rotation: the usable campaign mailbox with the most remaining capacity today."""
    best, best_room = None, None
    for m in campaign.mailboxes:
        if not usable(m, now):
            continue
        room = m.daily_limit - sent_today(db, m, now)
        if best_room is None or room > best_room:
            best, best_room = m.id, room
    return best


def dispatch_due(db: Session, now: datetime | None = None) -> list[uuid.UUID]:
    """Queue the next step for every due lead, then return queued messages that are due to send."""
    now = now or utcnow()
    # the prospect's next step must be sendable: active, and past its "not before" date. Prospects waiting on a
    # draft or paused step stay where they are (nothing is skipped) and are not even selected, so they never
    # crowd out prospects that can send. No next step at all means the sequence is finished.
    next_step = aliased(SequenceStep)
    due = list(db.scalars(
        select(CampaignContact)
        .join(Campaign, Campaign.id == CampaignContact.campaign_id)
        .outerjoin(next_step, and_(next_step.campaign_id == CampaignContact.campaign_id,
                                   next_step.position == CampaignContact.current_step + 1))
        .where(CampaignContact.status == "active", CampaignContact.next_send_at <= now,
               Campaign.status == "active", Campaign.deleted_at.is_(None),
               or_(next_step.id.is_(None),
                   and_(next_step.status == "active", or_(next_step.send_at.is_(None), next_step.send_at <= now))))
        .order_by(CampaignContact.next_send_at)
        .limit(BATCH)
        .with_for_update(of=CampaignContact, skip_locked=True)
    ))
    campaigns: dict[uuid.UUID, Campaign] = {}
    for cc in due:
        campaign = campaigns.get(cc.campaign_id) or db.get(Campaign, cc.campaign_id)
        campaigns[campaign.id] = campaign
        step = next((s for s in campaign.steps if s.position == cc.current_step + 1), None)
        if step is None:
            cc.status, cc.next_send_at, cc.stop_reason = "completed", None, "sequence_finished"
            continue
        if cc.email_account_id is None:  # a lead stays on one mailbox so follow-ups thread correctly
            cc.email_account_id = pick_mailbox(db, campaign, now)
            if cc.email_account_id is None:
                campaign.last_error = "No connected mailbox is available"
                continue
        db.execute(
            insert(ScheduledMessage).values(
                id=uuid.uuid4(), campaign_contact_id=cc.id, sequence_step_id=step.id, campaign_id=campaign.id,
                email_account_id=cc.email_account_id, status="queued", due_at=cc.next_send_at, attempts=0,
                created_at=now, updated_at=now,
            ).on_conflict_do_nothing(constraint="uq_scheduled_messages_cc_step")
        )
    db.flush()
    for campaign in campaigns.values():
        campaign_service.maybe_complete(db, campaign)
    ids = list(db.scalars(
        select(ScheduledMessage.id).where(ScheduledMessage.status == "queued", ScheduledMessage.due_at <= now)
        .order_by(ScheduledMessage.due_at).limit(BATCH)
    ))
    db.commit()
    return ids


# --- send -----------------------------------------------------------------------------------------

@dataclass
class _Reserved:
    sm_id: uuid.UUID
    cc_id: uuid.UUID
    mailbox_id: uuid.UUID
    step_position: int
    out: OutgoingMessage
    rendered: Rendered
    attachment_asset_id: uuid.UUID | None = None  # its bytes are read after reservation, outside the row locks


def _outgoing(mailbox: EmailAccount, contact: Contact, cc: CampaignContact, step: SequenceStep, rendered: Rendered,
              rfc_id: str, reply_to: str | None) -> OutgoingMessage:
    threaded = step.position > 1 and step.reply_in_thread and cc.last_message_id
    refs = list(dict.fromkeys(r for r in (cc.first_message_id, cc.last_message_id) if r)) if threaded else []
    return OutgoingMessage(
        from_email=mailbox.email, from_name=mailbox.display_name, to_email=contact.email,
        subject=rendered.subject, text=rendered.text, html=rendered.html, rfc_message_id=rfc_id,
        in_reply_to=cc.last_message_id if threaded else None, references=refs,
        provider_thread_id=cc.provider_thread_id if threaded else None,
        reply_to=reply_to, list_unsubscribe_url=unsubscribe_url(contact.id, cc.campaign_id),
    )


def _reserve(sm_id: uuid.UUID) -> _Reserved | str:
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == sm_id).with_for_update())
        if sm is None or sm.status != "queued":
            return "skipped"
        now = utcnow()
        if sm.due_at > now:
            return "not_due"
        cc = db.scalar(select(CampaignContact).where(CampaignContact.id == sm.campaign_contact_id).with_for_update())
        campaign = db.get(Campaign, sm.campaign_id)
        contact = db.get(Contact, cc.contact_id)
        step = db.get(SequenceStep, sm.sequence_step_id)

        def cancel(reason: str, cc_status: str | None = None) -> str:
            sm.status, sm.last_error = "cancelled", reason
            if cc_status and cc.status == "active":
                cc.status, cc.stop_reason, cc.next_send_at = cc_status, reason, None
            db.commit()
            return reason

        def defer(at: datetime, reason: str) -> str:
            sm.due_at, sm.last_error = at, reason
            cc.next_send_at = at
            db.commit()
            return "deferred"

        if campaign.status != "active":
            db.delete(sm)  # paused mid-flight: resume re-queues from next_send_at
            db.commit()
            return "campaign_inactive"
        if cc.status != "active":
            return cancel(f"prospect {cc.status}")
        if contact is None or contact.deleted_at:
            return cancel("contact deleted", "stopped")
        if is_suppressed(db, contact.email):
            return cancel("suppressed", "unsubscribed")
        if contact.verification_status == "invalid":
            return cancel("invalid email", "stopped")

        mailbox = db.scalar(select(EmailAccount).where(EmailAccount.id == sm.email_account_id).with_for_update())
        if (mailbox is None or not usable(mailbox, now)) and cc.current_step == 0:
            # nothing sent yet, so the lead can move to another mailbox
            other = pick_mailbox(db, campaign, now)
            if other:
                mailbox = db.scalar(select(EmailAccount).where(EmailAccount.id == other).with_for_update())
                sm.email_account_id = cc.email_account_id = other
        if mailbox is None or mailbox.status != "connected":
            return defer(now + timedelta(minutes=15), "mailbox unavailable")
        if mailbox.circuit_open_until and mailbox.circuit_open_until > now:
            return defer(mailbox.circuit_open_until, "mailbox circuit breaker open")
        if blocked := send_block_reason(mailbox, "campaign", contact.email):
            return defer(now + timedelta(hours=1), blocked)
        if problem := video_assets.step_problem(db, step, {mailbox.provider}):
            # never sent without the video it promises; waits (visibly) until the video or the limit is fixed
            campaign.last_error = problem
            return defer(now + timedelta(hours=1), problem)

        schedule = campaign.schedule or {}
        recipient_tz = contact.timezone if schedule.get("use_recipient_timezone") else None
        window = next_joint_window(now, [(mailbox.schedule or {}, None), (schedule, recipient_tz)])
        if window > now:
            return defer(window, "outside sending window")
        if sent_today(db, mailbox, now) >= mailbox.daily_limit:
            tomorrow = start_of_local_day(now, (mailbox.schedule or {}).get("timezone")) + timedelta(days=1)
            return defer(next_window_start(tomorrow, mailbox.schedule or {}), "mailbox daily limit reached")
        if campaign_sent_today(db, campaign, now) >= campaign.daily_limit:
            tomorrow = start_of_local_day(now, schedule.get("timezone")) + timedelta(days=1)
            return defer(next_window_start(tomorrow, schedule), "campaign daily limit reached")
        if mailbox.last_sent_at and now < mailbox.last_sent_at + timedelta(seconds=mailbox.min_interval_seconds):
            return defer(mailbox.last_sent_at + timedelta(seconds=mailbox.min_interval_seconds), "minimum spacing")

        try:
            rendered = render_step(db, campaign, step, cc, contact)
        except MissingVariables as exc:
            sm.status, sm.last_error = "failed", str(exc)
            cc.status, cc.stop_reason, cc.next_send_at = "error", "missing_variables", None
            db.add(EmailEvent(type="failed", contact_id=contact.id, campaign_id=campaign.id,
                              email_account_id=mailbox.id, data={"reason": str(exc)}))
            db.commit()
            return "missing_variables"

        rfc_id = new_message_id(mailbox.email)
        sm.status, sm.reserved_at, sm.rfc_message_id, sm.last_error = "sending", now, rfc_id, None
        sm.attempts += 1
        mailbox.last_sent_at = now  # reserves the spacing slot
        reply_to = app_settings.get_section(db, "sending_identity").get("reply_to") or None
        out = _outgoing(mailbox, contact, cc, step, rendered, rfc_id, reply_to)
        if step.video_preview:
            out.inline_images = video_assets.inline_images(step.video_asset)
        attachment_asset_id = step.video_asset_id if step.video_attach else None
        db.add(EmailEvent(type="attempted", contact_id=contact.id, campaign_id=campaign.id, email_account_id=mailbox.id,
                          data={"step": step.position}))
        db.commit()
        return _Reserved(sm.id, cc.id, mailbox.id, step.position, out, rendered, attachment_asset_id)


def _provider_for(mailbox_id: uuid.UUID):
    with SessionLocal() as db:
        return get_provider(db.get(EmailAccount, mailbox_id), "campaign")


def send_scheduled(sm_id: uuid.UUID) -> str:
    """Send one queued message. Safe to call concurrently and repeatedly for the same id."""
    reserved = _reserve(sm_id)
    if isinstance(reserved, str):
        return reserved
    if reserved.step_position > 1:
        # just-in-time check: a reply processed after reservation cancels the follow-up
        with SessionLocal() as db:
            if db.scalar(select(CampaignContact.status).where(CampaignContact.id == reserved.cc_id)) != "active":
                sm = db.get(ScheduledMessage, sm_id)
                sm.status, sm.last_error = "cancelled", "prospect stopped before send"
                db.commit()
                return "cancelled"
    if reserved.attachment_asset_id:
        # outside the send's error handling: nothing has gone out yet, so this must not count as an ambiguous send
        try:
            with SessionLocal() as db:
                reserved.out.attachments = [video_assets.load_attachment(db, reserved.attachment_asset_id)]
        except Exception as exc:
            logger.exception("could not load video attachment")
            _temporary_failure(reserved, f"Could not load the video attachment: {exc}")
            return "retry"
    try:
        result = _provider_for(reserved.mailbox_id).send(reserved.out)
    except AmbiguousSendError as exc:
        _mark(sm_id, "unknown", str(exc))
        return "unknown"
    except ProviderAuthError as exc:
        _mailbox_failure(reserved, "auth_error", str(exc), requeue=True)
        return "auth_error"
    except ProviderUnavailable as exc:
        _mailbox_failure(reserved, "error", str(exc), requeue=True)
        return "provider_unavailable"
    except PermanentProviderError as exc:
        _permanent_failure(reserved, str(exc))
        return "failed"
    except ProviderError as exc:  # temporary
        _temporary_failure(reserved, str(exc))
        return "retry"
    except Exception as exc:  # unknown outcome: never risk a duplicate, reconcile instead
        logger.exception("unexpected send error")
        _mark(sm_id, "unknown", f"{type(exc).__name__}: {exc}")
        return "unknown"
    record_success(sm_id, result, reserved.rendered)
    return "sent"


def _mark(sm_id: uuid.UUID, status: str, error: str | None) -> None:
    with SessionLocal() as db:
        sm = db.get(ScheduledMessage, sm_id)
        sm.status, sm.last_error = status, error
        db.commit()


def _mailbox_failure(r: _Reserved, status: str, error: str, *, requeue: bool) -> None:
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == r.sm_id).with_for_update())
        mailbox = db.scalar(select(EmailAccount).where(EmailAccount.id == r.mailbox_id).with_for_update())
        now = utcnow()
        mailbox.status, mailbox.last_error, mailbox.last_error_at = status, error[:2000], now
        if requeue:
            sm.status, sm.last_error, sm.due_at = "queued", error[:2000], now + timedelta(minutes=15)
            sm.attempts = max(0, sm.attempts - 1)
        db.commit()
    log(logger, logging.WARNING, "mailbox_error", mailbox_id=str(r.mailbox_id), status=status)


def _permanent_failure(r: _Reserved, error: str) -> None:
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == r.sm_id).with_for_update())
        cc = db.scalar(select(CampaignContact).where(CampaignContact.id == r.cc_id).with_for_update())
        sm.status, sm.last_error = "failed", error[:2000]
        if cc.status == "active":
            cc.status, cc.stop_reason, cc.next_send_at = "error", "send_failed", None
        db.add(EmailEvent(type="failed", contact_id=cc.contact_id, campaign_id=cc.campaign_id,
                          email_account_id=r.mailbox_id, data={"reason": error[:500]}))
        db.commit()


def _temporary_failure(r: _Reserved, error: str) -> None:
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == r.sm_id).with_for_update())
        mailbox = db.scalar(select(EmailAccount).where(EmailAccount.id == r.mailbox_id).with_for_update())
        now = utcnow()
        mailbox.consecutive_failures += 1
        mailbox.last_error, mailbox.last_error_at = error[:2000], now
        if mailbox.consecutive_failures >= CIRCUIT_THRESHOLD:
            mailbox.circuit_open_until = now + timedelta(minutes=CIRCUIT_MINUTES)
        db.commit()
    if _attempts(r.sm_id) >= MAX_ATTEMPTS:
        _permanent_failure(r, f"gave up after {MAX_ATTEMPTS} attempts: {error}")
        return
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == r.sm_id).with_for_update())
        backoff = timedelta(minutes=min(60, 2 ** sm.attempts))
        sm.status, sm.last_error, sm.due_at = "queued", error[:2000], utcnow() + backoff
        db.commit()


def _attempts(sm_id: uuid.UUID) -> int:
    with SessionLocal() as db:
        return db.scalar(select(ScheduledMessage.attempts).where(ScheduledMessage.id == sm_id)) or 0


def get_or_create_conversation(db: Session, mailbox_id: uuid.UUID, thread_id: str, *, contact_id=None,
                               campaign_id=None, subject: str | None = None) -> Conversation:
    conv = db.scalar(select(Conversation).where(
        Conversation.email_account_id == mailbox_id, Conversation.provider_thread_id == thread_id))
    if conv is None:
        conv = Conversation(email_account_id=mailbox_id, provider_thread_id=thread_id, contact_id=contact_id,
                            campaign_id=campaign_id, subject=subject, has_inbound=False, is_read=True)
        db.add(conv)
        db.flush()
    conv.contact_id = conv.contact_id or contact_id
    conv.campaign_id = conv.campaign_id or campaign_id
    conv.subject = conv.subject or subject
    return conv


def record_success(sm_id: uuid.UUID, result: SendResult, rendered: Rendered) -> None:
    with SessionLocal() as db:
        sm = db.scalar(select(ScheduledMessage).where(ScheduledMessage.id == sm_id).with_for_update())
        cc = db.scalar(select(CampaignContact).where(CampaignContact.id == sm.campaign_contact_id).with_for_update())
        campaign = db.get(Campaign, sm.campaign_id)
        contact = db.get(Contact, cc.contact_id)
        step = db.get(SequenceStep, sm.sequence_step_id)
        mailbox = db.get(EmailAccount, sm.email_account_id)
        now = utcnow()
        thread_id = result.provider_thread_id or result.provider_message_id
        conv = get_or_create_conversation(db, mailbox.id, thread_id, contact_id=contact.id, campaign_id=campaign.id,
                                          subject=rendered.subject)
        conv.last_message_at = now
        sent = SentMessage(
            scheduled_message_id=sm.id, email_account_id=mailbox.id, conversation_id=conv.id, campaign_id=campaign.id,
            contact_id=contact.id, kind="sequence", to_email=contact.email, from_email=mailbox.email,
            subject=rendered.subject, body_html=rendered.html, rfc_message_id=sm.rfc_message_id,
            provider_message_id=result.provider_message_id, provider_thread_id=thread_id, sent_at=now,
        )
        db.add(sent)
        db.flush()
        db.add(EmailEvent(type="accepted", contact_id=contact.id, campaign_id=campaign.id, email_account_id=mailbox.id,
                          sent_message_id=sent.id, data={"step": step.position}))
        sm.status, sm.last_error = "sent", None
        mailbox.consecutive_failures, mailbox.circuit_open_until = 0, None

        cc.current_step = max(cc.current_step, step.position)
        cc.provider_thread_id = cc.provider_thread_id or thread_id
        cc.first_message_id = cc.first_message_id or sm.rfc_message_id
        cc.last_message_id = sm.rfc_message_id
        if step.position == 1 or not cc.first_subject:
            cc.first_subject = rendered.subject
        if cc.status == "active":
            nxt = next((s for s in campaign.steps if s.position == step.position + 1), None)
            if nxt is None:
                cc.status, cc.next_send_at, cc.stop_reason = "completed", None, "sequence_finished"
            else:
                schedule = campaign.schedule or {}
                tz = contact.timezone if schedule.get("use_recipient_timezone") else schedule.get("timezone")
                cc.next_send_at = add_delay(now, nxt.delay_days, business_days=nxt.business_days, tz_name=tz,
                                            sending_days=set(schedule.get("days") or []))
        contact.last_contacted_at = now
        if contact.lead_status == "new_lead":
            set_lead_status(db, contact, "contacted", None, reason="email_sent")
        log_activity(db, contact.id, "email_sent", f"Step {step.position} sent: “{rendered.subject}”",
                     campaign_id=str(campaign.id), sent_message_id=str(sent.id))
        db.flush()  # sessions don't autoflush; the completion check counts rows in SQL
        campaign_service.maybe_complete(db, campaign)
        db.commit()


def reconcile_unknown(now: datetime | None = None) -> dict:
    """Ambiguous sends: look for our Message-ID at the provider before deciding sent vs. re-queue."""
    now = now or utcnow()
    out = {"sent": 0, "requeued": 0, "unresolved": 0}
    with SessionLocal() as db:
        ids = list(db.scalars(select(ScheduledMessage.id).where(
            ScheduledMessage.status == "unknown", ScheduledMessage.reserved_at <= now - RECONCILE_AFTER)))
    for sm_id in ids:
        with SessionLocal() as db:
            sm = db.get(ScheduledMessage, sm_id)
            mailbox = db.get(EmailAccount, sm.email_account_id)
            cc = db.get(CampaignContact, sm.campaign_contact_id)
            try:
                found = get_provider(mailbox).find_sent(sm.rfc_message_id)
            except ProviderError:
                out["unresolved"] += 1
                continue
            if found is None:
                sm.status, sm.due_at, sm.last_error = "queued", now, "not found at provider; re-queued"
                db.commit()
                out["requeued"] += 1
                continue
            rendered = render_step(db, db.get(Campaign, sm.campaign_id), db.get(SequenceStep, sm.sequence_step_id),
                                   cc, db.get(Contact, cc.contact_id))
        record_success(sm_id, found, rendered)
        out["sent"] += 1
    return out


def send_due(now: datetime | None = None) -> dict:
    """Dispatch and send everything that is due, sequentially. Used by the dev engine and tests."""
    with SessionLocal() as db:
        ids = dispatch_due(db, now)
    counts: dict[str, int] = {}
    started = time.monotonic()
    for sm_id in ids:
        if time.monotonic() - started > SEND_BUDGET_SECONDS:
            counts["left_for_next_pass"] = len(ids) - sum(counts.values())
            break
        outcome = send_scheduled(sm_id)
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts
