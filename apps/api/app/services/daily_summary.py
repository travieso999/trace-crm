"""End-of-day summary email to the operator: what went out today, replies, problems, and what needs attention.

Runs inside the engine pass the pinger already triggers, so it adds no database wake-ups of its own. Sent once per
local day (claimed under a row lock, so two engines can't both send it), on the default schedule's sending days, at the
first pass after Settings → Notifications → daily_summary_hour. Goes through the notification SMTP (notify_email),
never a campaign mailbox, and names no prospect beyond counts and campaign names.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import log
from app.models import AppSetting, Campaign, CampaignContact, EmailEvent, ScheduledMessage, SentMessage
from app.models.base import utcnow
from app.services import app_settings, attention, notify_email
from app.services.scheduling import start_of_local_day

logger = logging.getLogger("app.daily_summary")
STATE_KEY = "daily_summary_state"  # app_settings row holding {"last_sent": "YYYY-MM-DD"}; not a settings section


def tick(now: datetime | None = None) -> bool:
    """Send today's summary if it's due and not sent yet. Never raises: a mail problem is logged and retried on the
    next pass."""
    now = now or utcnow()
    if not get_settings().notify_email_configured:
        return False
    try:
        with SessionLocal() as db:
            n = app_settings.get_section(db, "notifications")
            to = (n.get("daily_summary_email") or n.get("notification_email") or "").strip()
            defaults = app_settings.get_section(db, "defaults")
            tz_name = defaults.get("timezone") or "America/New_York"
            local = now.astimezone(ZoneInfo(tz_name))
            days = set((defaults.get("schedule") or {}).get("days") or [0, 1, 2, 3, 4])
            if not n.get("daily_summary") or not to or local.weekday() not in days or local.hour < int(n.get("daily_summary_hour", 17)):
                return False
            today = local.date().isoformat()
            db.execute(insert(AppSetting).values(key=STATE_KEY, value={}, updated_at=now).on_conflict_do_nothing())
            state = db.scalar(select(AppSetting).where(AppSetting.key == STATE_KEY).with_for_update(skip_locked=True))
            if state is None or (state.value or {}).get("last_sent") == today:
                return False
            subject, text = build(db, now, tz_name)
            notify_email.send(to, subject, text)
            state.value = {**(state.value or {}), "last_sent": today}
            db.commit()
            log(logger, logging.INFO, "daily_summary_sent", date=today)
            return True
    except notify_email.NotifyEmailError as exc:
        log(logger, logging.WARNING, "daily_summary_failed", error=str(exc))
    except Exception:  # never let the summary break the sending engine
        logger.exception("daily_summary_error")
    return False


def build(db: Session, now: datetime, tz_name: str) -> tuple[str, str]:
    since = start_of_local_day(now, tz_name)
    local = now.astimezone(ZoneInfo(tz_name))
    sent_rows = db.execute(
        select(Campaign.name, func.count()).select_from(SentMessage).join(Campaign, Campaign.id == SentMessage.campaign_id)
        .where(SentMessage.kind == "sequence", SentMessage.sent_at >= since)
        .group_by(Campaign.name).order_by(func.count().desc())).all()
    sent = sum(n for _, n in sent_rows)
    ev = dict(db.execute(select(EmailEvent.type, func.count()).where(EmailEvent.occurred_at >= since)
                         .group_by(EmailEvent.type)).all())
    not_sent = db.scalar(select(func.count()).select_from(ScheduledMessage).where(
        ScheduledMessage.status == "cancelled", ScheduledMessage.updated_at >= since,
        ScheduledMessage.last_error.like("Not sent:%"))) or 0
    taken_out = db.scalar(select(func.count()).select_from(CampaignContact).where(
        CampaignContact.stop_reason == "missing_data", CampaignContact.updated_at >= since)) or 0
    failed = db.execute(select(ScheduledMessage.last_error, func.count()).where(
        ScheduledMessage.status == "failed", ScheduledMessage.updated_at >= since)
        .group_by(ScheduledMessage.last_error).order_by(func.count().desc()).limit(5)).all()
    queued = db.scalar(select(func.count()).select_from(ScheduledMessage).where(ScheduledMessage.status == "queued")) or 0
    items = attention.items(db)
    base = get_settings().public_url.rstrip("/")

    lines = [f"Trace CRM, {local:%A %B} {local.day}", ""]
    lines.append(f"Sent today: {sent}" + (f" ({queued} queued for later)" if queued else ""))
    for name, n in sent_rows[:15]:
        lines.append(f"  - {name}: {n}")
    replies = ev.get("replied", 0)
    lines += ["", f"Replies: {replies}" + (f" ({ev.get('positive_reply', 0)} positive)" if replies else ""),
              f"Bounces: {ev.get('bounced', 0)}", f"Unsubscribes: {ev.get('unsubscribed', 0)}"]
    lines += ["", "Not sent today:"]
    if not (not_sent or taken_out or failed):
        lines.append("  Nothing: every due email went out.")
    if taken_out:
        lines.append(f"  - {taken_out} prospect(s) left out for a missing name or company (they go back in once it's filled in)")
    for err, n in failed:
        lines.append(f"  - {n} refused by the mail provider: {(err or 'unknown error')[:160]}")
    lines += ["", "Needs attention:"]
    if not items:
        lines.append("  Nothing: campaigns are running normally.")
    for it in items:
        lines.append(f"  - {it['title']}. {it['detail']} {base}{it['href']}")
    lines += ["", f"Open the CRM: {base}/", "", "Turn this email off or change the address in Settings > Notifications."]

    problems = sum(1 for it in items if it["severity"] == "error")
    subject = f"Trace CRM daily summary: {sent} sent, {replies} repl{'y' if replies == 1 else 'ies'}"
    if problems:
        subject += f", {problems} problem{'s' if problems != 1 else ''}"
    return subject, "\n".join(lines)
