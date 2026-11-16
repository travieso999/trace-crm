"""Read-only check: can a demo submission at a given moment be tied to a campaign recipient with data we hold?

    python -m app.cli attribution-check --at "2026-11-16T10:02:00-05:00" [--minutes 60] [--email typed@example.com]

It never writes and never guesses. It reports what exists (emails that contained the demo link, tracked links,
clicks, demo sessions near that moment) and states plainly when nothing links the visit to a person.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, inspect, or_, select
from sqlalchemy.orm import Session

from app.models import Campaign, Contact, DemoEvent, DemoSession, SentMessage, TrackedLink, TrackingEvent
from app.services import app_settings


def _demo_patterns(db: Session) -> list[str]:
    urls = app_settings.get_section(db, "tracking").get("demo_urls") or []
    pats = set()
    for u in urls:
        core = u.split("://", 1)[-1].rstrip("/")
        pats.add(core)
    return sorted(pats)


def run(db: Session, at: datetime, minutes: int = 60, email: str | None = None) -> list[str]:
    out: list[str] = []
    say = out.append
    lo, hi = at - timedelta(minutes=minutes), at + timedelta(minutes=minutes)
    say(f"Submission time checked: {at.isoformat()} (±{minutes} min)")
    pats = _demo_patterns(db)
    say(f"Demo link patterns: {', '.join(pats) or '(none configured)'}")

    # 1. emails that carried the demo link
    like = or_(*[SentMessage.body_html.ilike(f"%{p}%") for p in pats]) if pats else None
    if like is not None:
        n, first, last = db.execute(select(func.count(), func.min(SentMessage.sent_at), func.max(SentMessage.sent_at))
                                    .where(SentMessage.kind == "sequence", like)).one()
        say(f"Campaign emails containing the demo link: {n}" + (f" (first {first:%Y-%m-%d}, last {last:%Y-%m-%d})" if n else ""))
        for name, count in db.execute(select(Campaign.name, func.count()).select_from(SentMessage)
                                      .join(Campaign, Campaign.id == SentMessage.campaign_id)
                                      .where(SentMessage.kind == "sequence", like).group_by(Campaign.name)):
            say(f"  - {name}: {count}")
        recipients = db.scalar(select(func.count(func.distinct(SentMessage.contact_id))).where(
            SentMessage.kind == "sequence", like)) or 0
        say(f"Distinct recipients who were sent the demo link: {recipients}")

    # 2. tracking data
    if not inspect(db.get_bind()).has_table("tracked_links"):
        say("Tracking tables: NOT PRESENT (migration a7e3c9d14b62 not applied). No click data can exist.")
        tracked = 0
    else:
        tracked = db.scalar(select(func.count()).select_from(TrackedLink)) or 0
        first_tracked = db.scalar(select(func.min(TrackedLink.created_at)))
        say(f"Tracked links ever created: {tracked}" + (f" (since {first_tracked:%Y-%m-%d %H:%M} UTC)" if first_tracked else ""))
        clicks = db.execute(select(TrackingEvent, TrackedLink).join(TrackedLink, TrackedLink.id == TrackingEvent.link_id)
                            .where(TrackingEvent.type == "click", TrackingEvent.occurred_at.between(lo, hi))).all()
        say(f"Tracked clicks in the window: {len(clicks)}")
        for e, link in clicks:
            c = db.get(Contact, e.contact_id) if e.contact_id else None
            flag = "suspected scanner" if e.suspected_bot and not e.verified_human else "counted"
            say(f"  - {e.occurred_at.isoformat()} {link.kind} '{link.label}' -> {c.email if c else '?'} ({flag})")
        sessions = db.scalars(select(DemoSession).where(DemoSession.last_event_at.between(lo, hi))).all()
        say(f"Attributed demo sessions active in the window: {len(sessions)}")
        for s in sessions:
            c = db.get(Contact, s.contact_id) if s.contact_id else None
            kinds = sorted(set(db.scalars(select(DemoEvent.type).where(DemoEvent.demo_session_id == s.id))))
            say(f"  - {c.email if c else '?'}: {', '.join(kinds)}")

    # 3. a typed email (from the submission notice) that matches a prospect is reported as exactly that, no more
    if email:
        c = db.scalar(select(Contact).where(func.lower(Contact.email) == email.strip().lower(), Contact.deleted_at.is_(None)))
        if c:
            got = db.scalar(select(func.count()).where(SentMessage.contact_id == c.id, SentMessage.kind == "sequence")) or 0
            say(f"The email typed in the form matches prospect {c.email} ({got} campaign emails sent to them). This only "
                "shows what the visitor typed; the demo accepts any value, so it is not proof of who submitted.")
        else:
            say("The email typed in the form matches no prospect in the CRM.")

    # 4. conclusion
    say("")
    if tracked == 0:
        say("CONCLUSION: No per-recipient link tracking existed when these emails were sent. Every recipient got the "
            "same demo URL, and the demo's submission carries no campaign or recipient identifier. The submission "
            "cannot be attributed to a recipient from CRM data. Browser, OS and approximate city (e.g. Safari / Mac / "
            "Chicago) are shared by many people and are not used to identify anyone.")
    else:
        say("CONCLUSION: See the clicks and sessions above. Only a click or demo session listed in the window is "
            "evidence; anything sent before tracking started cannot be attributed.")
    return out
