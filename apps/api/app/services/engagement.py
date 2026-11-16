"""Read side of engagement tracking: the Email Tracking table, Engaged Prospects and a prospect's timeline.

Counting rules (shown in the UI too):
* clicks count only when not flagged as automated, or flagged but later proven human by a demo interaction;
  flagged clicks are reported separately as "scanner clicks";
* opens are estimates (image proxies and Apple Mail Privacy Protection) and exist only while open tracking is on;
* a demo visit is an attributed demo session that reported at least one event; an interaction is any step beyond
  viewing a page (starting the application, a form, signing, submitting).
* website batches from trace-studio.tech (services/site_tracking.py) add to these: a demo visit is a website session
  with ``session_start`` or a page under /demos/; completed is ``application_completed``; watched video is
  ``video_progress`` >= 75% or ``video_complete``; a ``callback_request`` makes the prospect High and puts them first.
"""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Integer, and_, case, cast, distinct, exists, func, literal, or_, select, union
from sqlalchemy.orm import Session

from app.models import (
    Campaign,
    CampaignContact,
    Contact,
    Conversation,
    CrmActivity,
    CrmOpportunity,
    CrmTask,
    DemoEvent,
    DemoSession,
    EmailEvent,
    ReceivedMessage,
    SentMessage,
    SequenceStep,
    SiteEvent,
    TrackedLink,
    TrackingEvent,
    User,
)
from app.services import site_tracking
from app.services.tracking import INTERACTIONS, human_click_filter

L, E = TrackedLink, TrackingEvent
LEVELS = ("high", "medium", "low")


def _day_start(d: date, tz_name: str = "America/New_York") -> datetime:
    """Date filters are whole days in the workspace's time zone (Eastern by default)."""
    return datetime.combine(d, time.min, tzinfo=ZoneInfo(tz_name))


def _name(c) -> str:
    return " ".join(p for p in (c.first_name, c.last_name) if p) or ""


# --- Email Tracking -------------------------------------------------------------------------------

def _click_agg():
    human = human_click_filter()
    return (
        select(
            L.sent_message_id.label("sid"),
            func.count(E.id).filter(human).label("clicks"),
            func.count(distinct(L.id)).filter(human).label("unique_clicks"),
            func.count(E.id).filter(E.type == "click", E.suspected_bot, E.verified_human.is_(False)).label("scanner_clicks"),
            func.count(E.id).filter(human, L.kind == "demo").label("demo_clicks"),
            func.min(E.occurred_at).filter(human).label("first_click"),
            func.max(E.occurred_at).filter(human).label("last_click"),
            func.count(E.id).filter(E.type == "open", E.suspected_bot.is_(False)).label("opens"),
            func.bool_or(L.kind == "open_pixel").label("open_tracked"),
            func.bool_or(L.kind != "open_pixel").label("links_tracked"),
        )
        .select_from(L).outerjoin(E, E.link_id == L.id)
        .where(L.sent_message_id.is_not(None))
        .group_by(L.sent_message_id)
        .subquery()
    )


def _demo_agg():
    return (
        select(
            L.sent_message_id.label("sid"),
            func.count(distinct(DemoSession.id)).filter(DemoSession.first_event_at.is_not(None)).label("demo_visits"),
            func.count(distinct(DemoEvent.demo_session_id)).filter(DemoEvent.type.in_(INTERACTIONS)).label("demo_interactions"),
            func.count(distinct(DemoEvent.demo_session_id)).filter(DemoEvent.type == "application_submitted").label("demo_completed"),
            func.max(DemoEvent.occurred_at).label("last_demo"),
        )
        .select_from(L).join(DemoSession, DemoSession.link_id == L.id)
        .outerjoin(DemoEvent, DemoEvent.demo_session_id == DemoSession.id)
        .where(L.sent_message_id.is_not(None))
        .group_by(L.sent_message_id)
        .subquery()
    )


def email_rows(db: Session, *, campaign_id: uuid.UUID | None = None, q: str | None = None,
               date_from: date | None = None, date_to: date | None = None, clicked: str | None = None,
               demo: str | None = None, contact_id: uuid.UUID | None = None, sort: str = "sent",
               page: int = 1, page_size: int = 50) -> dict:
    c, d = _click_agg(), _demo_agg()
    bounced = exists().where(EmailEvent.type == "bounced", EmailEvent.contact_id == SentMessage.contact_id,
                             EmailEvent.campaign_id == SentMessage.campaign_id,
                             EmailEvent.occurred_at >= SentMessage.sent_at)
    clicks = func.coalesce(c.c.clicks, 0)
    stmt = (
        select(
            SentMessage.id, SentMessage.sent_at, SentMessage.subject, SentMessage.to_email, SentMessage.campaign_id,
            SentMessage.contact_id, Contact.first_name, Contact.last_name, Contact.company_name, Campaign.name.label("campaign"),
            SequenceStep.position.label("step"), CampaignContact.replied_at, bounced.label("bounced"),
            clicks.label("clicks"), func.coalesce(c.c.unique_clicks, 0).label("unique_clicks"),
            func.coalesce(c.c.scanner_clicks, 0).label("scanner_clicks"), func.coalesce(c.c.demo_clicks, 0).label("demo_clicks"),
            c.c.first_click, c.c.last_click, c.c.opens, c.c.open_tracked, c.c.links_tracked,
            func.coalesce(d.c.demo_visits, 0).label("demo_visits"),
            func.coalesce(d.c.demo_interactions, 0).label("demo_interactions"),
            func.coalesce(d.c.demo_completed, 0).label("demo_completed"),
        )
        .join(Contact, Contact.id == SentMessage.contact_id)
        .outerjoin(Campaign, Campaign.id == SentMessage.campaign_id)
        .outerjoin(c, c.c.sid == SentMessage.id)
        .outerjoin(d, d.c.sid == SentMessage.id)
        .outerjoin(CampaignContact, and_(CampaignContact.campaign_id == SentMessage.campaign_id,
                                         CampaignContact.contact_id == SentMessage.contact_id))
        .outerjoin(SequenceStep, SequenceStep.id == select(L.sequence_step_id).where(
            L.sent_message_id == SentMessage.id).limit(1).scalar_subquery())
        .where(SentMessage.kind == "sequence")
    )
    if campaign_id:
        stmt = stmt.where(SentMessage.campaign_id == campaign_id)
    if contact_id:
        stmt = stmt.where(SentMessage.contact_id == contact_id)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    if date_from:
        stmt = stmt.where(SentMessage.sent_at >= _day_start(date_from))
    if date_to:
        stmt = stmt.where(SentMessage.sent_at < _day_start(date_to) + timedelta(days=1))
    if clicked == "yes":
        stmt = stmt.where(clicks > 0)
    elif clicked == "no":
        stmt = stmt.where(clicks == 0, c.c.links_tracked.is_(True))
    elif clicked == "untracked":
        stmt = stmt.where(or_(c.c.links_tracked.is_(None), c.c.links_tracked.is_(False)))
    if demo == "clicked":
        stmt = stmt.where(func.coalesce(c.c.demo_clicks, 0) > 0)
    elif demo == "visited":
        stmt = stmt.where(func.coalesce(d.c.demo_visits, 0) > 0)
    elif demo == "interacted":
        stmt = stmt.where(func.coalesce(d.c.demo_interactions, 0) > 0)
    elif demo == "completed":
        stmt = stmt.where(func.coalesce(d.c.demo_completed, 0) > 0)

    base = stmt.subquery()
    totals = db.execute(select(
        func.count(), func.count().filter(base.c.links_tracked.is_(True)), func.count().filter(base.c.clicks > 0),
        func.count().filter(base.c.demo_clicks > 0), func.count().filter(base.c.demo_visits > 0),
        func.count().filter(base.c.demo_completed > 0), func.coalesce(func.sum(base.c.scanner_clicks), 0),
    ).select_from(base)).one()
    order = {"sent": [SentMessage.sent_at.desc()],
             "last_click": [c.c.last_click.desc().nulls_last(), SentMessage.sent_at.desc()],
             "clicks": [clicks.desc(), SentMessage.sent_at.desc()]}.get(sort, [SentMessage.sent_at.desc()])
    rows = db.execute(stmt.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
    links = _links_for(db, [r.id for r in rows])
    return {
        "items": [_email_out(r, links.get(r.id, [])) for r in rows],
        "total": totals[0], "page": page, "page_size": page_size,
        "summary": {"emails": totals[0], "tracked": totals[1], "clicked": totals[2], "demo_clicked": totals[3],
                    "demo_visited": totals[4], "demo_completed": totals[5], "scanner_clicks": int(totals[6])},
    }


def _links_for(db: Session, sent_ids: list) -> dict:
    if not sent_ids:
        return {}
    human = human_click_filter()
    rows = db.execute(
        select(L.sent_message_id, L.id, L.kind, L.label, L.destination_url, L.ordinal,
               func.count(E.id).filter(human).label("clicks"),
               func.count(E.id).filter(E.type == "click", E.suspected_bot, E.verified_human.is_(False)).label("scanner"),
               func.min(E.occurred_at).filter(human).label("first"), func.max(E.occurred_at).filter(human).label("last"))
        .select_from(L).outerjoin(E, E.link_id == L.id)
        .where(L.sent_message_id.in_(sent_ids), L.kind != "open_pixel")
        .group_by(L.id).order_by(L.ordinal)).all()
    out: dict = {}
    for r in rows:
        out.setdefault(r.sent_message_id, []).append({
            "id": str(r.id), "kind": r.kind, "label": r.label, "url": r.destination_url, "clicks": r.clicks,
            "scanner_clicks": r.scanner, "first_click_at": _iso(r.first), "last_click_at": _iso(r.last)})
    return out


def _iso(v):
    return v.isoformat() if v else None


def _email_out(r, links: list) -> dict:
    delivery = "bounced" if r.bounced else "accepted"
    return {
        "sent_message_id": str(r.id), "contact_id": str(r.contact_id), "name": _name(r), "email": r.to_email,
        "company": r.company_name, "campaign_id": str(r.campaign_id) if r.campaign_id else None, "campaign": r.campaign,
        "step": r.step, "subject": r.subject, "sent_at": r.sent_at.isoformat(),
        # "accepted" = the provider took it; neither Gmail nor SMTP confirms delivery to an external inbox
        "delivery": delivery, "replied_at": _iso(r.replied_at),
        "tracked": bool(r.links_tracked), "opens": r.opens if r.open_tracked else None,
        "clicks": r.clicks, "unique_clicks": r.unique_clicks, "scanner_clicks": r.scanner_clicks,
        "demo_clicks": r.demo_clicks, "first_click_at": _iso(r.first_click), "last_click_at": _iso(r.last_click),
        "demo_visits": r.demo_visits, "demo_interactions": r.demo_interactions, "demo_completed": r.demo_completed,
        "links": links,
    }


# --- Engaged Prospects ----------------------------------------------------------------------------

def _engaged_query(campaign_id: uuid.UUID | None):
    human = human_click_filter()
    clk = select(
        E.contact_id.label("cid"),
        func.count(E.id).filter(human).label("clicks"),
        func.count(E.id).filter(human, L.kind == "demo").label("demo_clicks"),
        func.max(E.occurred_at).filter(human).label("last_click"),
        func.count(E.id).filter(E.type == "open", E.suspected_bot.is_(False)).label("opens"),
        func.max(E.occurred_at).filter(E.type == "open", E.suspected_bot.is_(False)).label("last_open"),
    ).join(L, L.id == E.link_id).where(E.contact_id.is_not(None))
    dem = select(
        DemoSession.contact_id.label("cid"),
        func.count(distinct(DemoSession.id)).filter(DemoSession.first_event_at.is_not(None)).label("demo_visits"),
        func.count(distinct(DemoEvent.demo_session_id)).filter(DemoEvent.type.in_(INTERACTIONS)).label("demo_interactions"),
        func.count(distinct(DemoEvent.demo_session_id)).filter(DemoEvent.type == "application_submitted").label("demo_completed"),
        func.max(DemoEvent.occurred_at).label("last_demo"),
    ).outerjoin(DemoEvent, DemoEvent.demo_session_id == DemoSession.id).where(DemoSession.contact_id.is_not(None))
    S = SiteEvent  # noqa: N806
    site_visit = or_(S.type == "session_start", and_(S.type == "page_view", S.page.like("/demos/%")))
    watched = or_(S.type == "video_complete",
                  and_(S.type == "video_progress", S.data["percent"].astext.in_(site_tracking.WATCHED_PERCENTS)))
    site_interaction = or_(S.type.in_(site_tracking.INTERACTIONS),
                           and_(S.type == "click", S.data["kind"].astext.in_(site_tracking.CONTACT_TAPS)))
    web = select(
        S.contact_id.label("cid"),
        func.count(distinct(S.sid)).filter(site_visit).label("visits"),
        func.count(distinct(S.sid)).filter(S.type == "application_completed").label("completed"),
        func.count(distinct(S.sid)).filter(watched).label("watched"),
        func.count(S.id).filter(site_interaction).label("interactions"),
        func.max(S.occurred_at).filter(S.type == "callback_request").label("callback_at"),
        func.max(S.occurred_at).label("last_site"),
    ).where(S.contact_id.is_not(None))
    rep = select(
        ReceivedMessage.contact_id.label("cid"), func.count(ReceivedMessage.id).label("replies"),
        func.max(ReceivedMessage.received_at).label("last_reply"),
    ).where(ReceivedMessage.kind == "reply", ReceivedMessage.contact_id.is_not(None))
    if campaign_id:
        clk = clk.where(E.campaign_id == campaign_id)
        dem = dem.where(DemoSession.campaign_id == campaign_id)
        web = web.where(S.campaign_id == campaign_id)
        rep = rep.join(Conversation, Conversation.id == ReceivedMessage.conversation_id).where(
            Conversation.campaign_id == campaign_id)
    clk = clk.group_by(E.contact_id).subquery()
    dem = dem.group_by(DemoSession.contact_id).subquery()
    rep = rep.group_by(ReceivedMessage.contact_id).subquery()
    web = web.group_by(S.contact_id).subquery()
    ids = union(select(clk.c.cid), select(dem.c.cid), select(rep.c.cid), select(web.c.cid)).subquery()

    n = lambda col: func.coalesce(col, 0)  # noqa: E731
    visits = n(dem.c.demo_visits) + n(web.c.visits)
    interactions = n(dem.c.demo_interactions) + n(web.c.interactions)
    completed = n(dem.c.demo_completed) + n(web.c.completed)
    level = case(
        (or_(interactions > 0, completed > 0, web.c.callback_at.is_not(None)), literal("high")),
        (or_(n(clk.c.clicks) > 0, visits > 0, n(rep.c.replies) > 0, n(web.c.watched) > 0), literal("medium")),
        (n(clk.c.opens) > 0, literal("low")),
        else_=None,
    )
    last = func.greatest(clk.c.last_click, clk.c.last_open, dem.c.last_demo, rep.c.last_reply, web.c.last_site)
    open_tasks = select(func.count(CrmTask.id)).where(
        CrmTask.contact_id == Contact.id, CrmTask.completed_at.is_(None)).scalar_subquery()
    stmt = (
        select(
            Contact.id, Contact.first_name, Contact.last_name, Contact.email, Contact.company_name, Contact.lead_status,
            n(clk.c.clicks).label("clicks"), n(clk.c.demo_clicks).label("demo_clicks"), n(clk.c.opens).label("opens"),
            visits.label("demo_visits"), interactions.label("demo_interactions"),
            completed.label("demo_completed"), n(web.c.watched).label("watched_video"), n(rep.c.replies).label("replies"),
            web.c.callback_at.label("callback_at"), last.label("last_engagement_at"), level.label("level"),
            CrmOpportunity.owner_id, cast(open_tasks, Integer).label("open_tasks"),
        )
        .select_from(ids)
        .join(Contact, Contact.id == ids.c.cid)
        .outerjoin(clk, clk.c.cid == ids.c.cid)
        .outerjoin(dem, dem.c.cid == ids.c.cid)
        .outerjoin(rep, rep.c.cid == ids.c.cid)
        .outerjoin(web, web.c.cid == ids.c.cid)
        .outerjoin(CrmOpportunity, CrmOpportunity.contact_id == Contact.id)
        .where(Contact.deleted_at.is_(None), level.is_not(None))
    )
    return stmt, level, last


SORTS = ("last_engagement", "demo_clicks", "demo_visits", "demo_completed", "watched_video", "replies", "clicks")


def engaged(db: Session, *, campaign_id: uuid.UUID | None = None, level: str | None = None,
            owner: str | None = None, me: User | None = None, q: str | None = None, sort: str = "last_engagement",
            page: int = 1, page_size: int = 50) -> dict:
    stmt, level_expr, last = _engaged_query(campaign_id)
    if level in LEVELS:
        stmt = stmt.where(level_expr == level)
    if owner == "me" and me:
        stmt = stmt.where(CrmOpportunity.owner_id == me.id)
    elif owner == "unassigned":
        stmt = stmt.where(CrmOpportunity.owner_id.is_(None))
    elif owner:
        try:
            stmt = stmt.where(CrmOpportunity.owner_id == uuid.UUID(owner))
        except ValueError:
            pass
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(func.lower(Contact.email).like(like), func.lower(Contact.first_name).like(like),
                              func.lower(Contact.last_name).like(like), func.lower(Contact.company_name).like(like)))
    sub = stmt.subquery()
    total = db.scalar(select(func.count()).select_from(sub)) or 0
    counts = dict(db.execute(select(sub.c.level, func.count()).group_by(sub.c.level)).all())
    key = sort if sort in SORTS else "last_engagement"
    col = sub.c.last_engagement_at if key == "last_engagement" else sub.c[key]
    # a prospect who asked for a call back is always first, whatever the sort
    rows = db.execute(select(sub).order_by(sub.c.callback_at.desc().nulls_last(), col.desc().nulls_last(),
                                           sub.c.last_engagement_at.desc().nulls_last())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    owners = {u.id: u.display_name for u in db.scalars(
        select(User).where(User.id.in_({r.owner_id for r in rows if r.owner_id})))} if rows else {}
    campaigns = _campaigns_for(db, [r.id for r in rows])
    return {
        "items": [{
            "contact_id": str(r.id), "name": _name(r), "email": r.email, "company": r.company_name,
            "lead_status": r.lead_status, "level": r.level, "clicks": r.clicks, "demo_clicks": r.demo_clicks,
            "opens": r.opens, "demo_visits": r.demo_visits, "demo_interactions": r.demo_interactions,
            "demo_completed": r.demo_completed, "watched_video": r.watched_video, "replies": r.replies,
            "callback_requested_at": _iso(r.callback_at), "last_engagement_at": _iso(r.last_engagement_at),
            "campaigns": campaigns.get(r.id, []),
            "owner": {"id": str(r.owner_id), "name": owners.get(r.owner_id, "Former user")} if r.owner_id else None,
            "open_tasks": r.open_tasks or 0,
        } for r in rows],
        "total": total, "page": page, "page_size": page_size,
        "levels": {lv: counts.get(lv, 0) for lv in LEVELS},
    }


def _campaigns_for(db: Session, contact_ids: list) -> dict:
    if not contact_ids:
        return {}
    rows = db.execute(select(distinct(L.contact_id), Campaign.id, Campaign.name).join(Campaign, Campaign.id == L.campaign_id)
                      .where(L.contact_id.in_(contact_ids))).all()
    out: dict = {}
    for cid, camp_id, name in rows:
        out.setdefault(cid, []).append({"id": str(camp_id), "name": name})
    return out


def level_for(db: Session, contact_id: uuid.UUID) -> str | None:
    stmt, _, _ = _engaged_query(None)
    sub = stmt.subquery()
    return db.scalar(select(sub.c.level).where(sub.c.id == contact_id))


# --- timeline -------------------------------------------------------------------------------------

def timeline(db: Session, contact_id: uuid.UUID, *, raw: bool = False) -> dict:
    """Deduplicated story (one entry per email, link, demo session step) plus, with ``raw``, every underlying event."""
    camp = {cid: name for cid, name in db.execute(select(Campaign.id, Campaign.name))}
    items: list[dict] = []

    def add(kind: str, at, summary: str, campaign_id=None, **extra):
        items.append({"kind": kind, "at": at.isoformat(), "summary": summary,
                      "campaign_id": str(campaign_id) if campaign_id else None,
                      "campaign": camp.get(campaign_id), **extra})

    sent = db.execute(select(SentMessage.id, SentMessage.sent_at, SentMessage.subject, SentMessage.campaign_id,
                             SentMessage.kind)
                      .where(SentMessage.contact_id == contact_id, SentMessage.kind != "test")).all()
    for s in sent:
        add("email_sent", s.sent_at, f"Email sent: “{s.subject}”" if s.kind == "sequence" else f"Reply sent: “{s.subject}”",
            s.campaign_id, sent_message_id=str(s.id))
    human = human_click_filter()
    # first estimated open per email
    for sid, at, cid in db.execute(
            select(L.sent_message_id, func.min(E.occurred_at), L.campaign_id).join(E, E.link_id == L.id)
            .where(L.contact_id == contact_id, E.type == "open", E.suspected_bot.is_(False))
            .group_by(L.sent_message_id, L.campaign_id)):
        add("email_opened", at, "Email opened (estimated, unverified)", cid, sent_message_id=str(sid) if sid else None)
    # first human click per link, with how many times in all
    for link_id, kind, label, url, cid, first, n in db.execute(
            select(L.id, L.kind, L.label, L.destination_url, L.campaign_id, func.min(E.occurred_at), func.count(E.id))
            .join(E, E.link_id == L.id).where(L.contact_id == contact_id, human)
            .group_by(L.id)):
        what = label or url or "a link"
        add("demo_click" if kind == "demo" else "email_click", first, f"Clicked “{what}”", cid,
            url=url, link_id=str(link_id), count=n)
    # demo sessions: first visit, start, completion each once per session
    sessions = db.scalars(select(DemoSession).where(DemoSession.contact_id == contact_id)).all()
    for sess in sessions:
        evs = db.scalars(select(DemoEvent).where(DemoEvent.demo_session_id == sess.id).order_by(DemoEvent.occurred_at)).all()
        def first(types, evs=evs):
            return next((e for e in evs if e.type in types), None)
        if v := first(("page_view",)) or (evs[0] if evs else None):
            add("demo_visit", v.occurred_at, "Visited the demo", sess.campaign_id, demo_session_id=str(sess.id))
        if s := first(("application_started", "application_step")):
            add("demo_application_started", s.occurred_at, "Started Bail Application Demo", sess.campaign_id,
                demo_session_id=str(sess.id))
        if done := first(("application_submitted",)):
            add("demo_application_completed", done.occurred_at,
                "Completed Bail Application Demo" + (" — Signed Electronically" if done.signed else ""),
                sess.campaign_id, demo_session_id=str(sess.id), sample=done.sample, signed=done.signed)
        for form in sorted({e.form for e in evs if e.type == "form_submitted" and e.form}):
            first_form = next(e for e in evs if e.type == "form_submitted" and e.form == form)
            add("demo_form", first_form.occurred_at, f"Tried the demo's {form.replace('_', ' ')} feature",
                sess.campaign_id, demo_session_id=str(sess.id), form=form)
    for r in db.execute(select(ReceivedMessage.received_at, ReceivedMessage.subject, Conversation.campaign_id)
                        .outerjoin(Conversation, Conversation.id == ReceivedMessage.conversation_id)
                        .where(ReceivedMessage.contact_id == contact_id, ReceivedMessage.kind == "reply")):
        add("email_replied", r.received_at, f"Replied: “{r.subject or '(no subject)'}”", r.campaign_id)
    # website milestones (one line each, written at ingest by site_tracking._milestone and apply_identify)
    for a in db.scalars(select(CrmActivity).where(CrmActivity.contact_id == contact_id, CrmActivity.type.like("site_%"))):
        d = a.data or {}
        add(a.type, a.created_at, a.summary, uuid.UUID(d["campaign_id"]) if d.get("campaign_id") else None,
            site=d.get("site"), page=d.get("page"))
    items.sort(key=lambda i: i["at"], reverse=True)
    out = {"items": items, "level": level_for(db, contact_id), "visits": site_tracking.visits(db, contact_id)}
    if raw:
        out["raw"] = _raw(db, contact_id, camp)
    return out


def _raw(db: Session, contact_id: uuid.UUID, camp: dict) -> list[dict]:
    events = []
    for e, link in db.execute(select(E, L).join(L, L.id == E.link_id).where(E.contact_id == contact_id)
                              .order_by(E.occurred_at.desc()).limit(500)):
        events.append({"source": "email", "type": e.type, "at": e.occurred_at.isoformat(), "link": link.label,
                       "url": link.destination_url, "suspected_bot": e.suspected_bot, "verified_human": e.verified_human,
                       "reasons": e.bot_reasons or [], "user_agent": e.user_agent, "campaign": camp.get(e.campaign_id)})
    for d in db.scalars(select(DemoEvent).where(DemoEvent.contact_id == contact_id)
                        .order_by(DemoEvent.occurred_at.desc()).limit(500)):
        events.append({"source": "demo", "type": d.type, "at": d.occurred_at.isoformat(), "page": d.page,
                       "form": d.form, "step": d.step, "sample": d.sample, "signed": d.signed,
                       "user_agent": d.user_agent, "campaign": camp.get(d.campaign_id)})
    for s in db.scalars(select(SiteEvent).where(SiteEvent.contact_id == contact_id)
                        .order_by(SiteEvent.occurred_at.desc()).limit(500)):
        events.append({"source": "site", "type": s.type, "at": s.occurred_at.isoformat(), "page": s.page, "site": s.site,
                       "data": s.data, "place": ", ".join(p for p in (s.city, s.region, s.country) if p) or None,
                       "user_agent": s.user_agent, "campaign": camp.get(s.campaign_id)})
    return sorted(events, key=lambda x: x["at"], reverse=True)
