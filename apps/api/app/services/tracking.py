"""Click, open and demo-engagement tracking.

Flow
----
1. Sending: ``apply_to_email`` rewrites each eligible ``href`` in a campaign email to ``{TRACKING_BASE_URL}/t/<token>``
   (one random token per link per recipient) and, when open tracking is on, adds a 1x1 pixel. Unsubscribe, mailto/tel
   and authentication-style links are never touched. Retries and reconciles of the same scheduled message reuse the
   same tokens, so the email that actually went out always resolves.
2. Click: ``record_click`` logs the raw event, judges whether it looks automated, and returns the stored destination.
   Only stored destinations are ever redirected to, so there is no open redirect. A click on a demo link also starts a
   ``DemoSession`` and appends ``trc=<session token>`` to the demo URL.
3. Demo: the demo page keeps that token in its own (first-party) storage and posts step events to
   ``/api/track/demo``. Only the step, page, form name and two booleans are accepted; whatever the visitor typed is
   never sent or stored.

Nothing in a URL identifies a recipient: tokens are random and only resolve through the database.
"""

import hashlib
import hmac
import html
import logging
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Campaign,
    Contact,
    CrmActivity,
    CrmOpportunity,
    DemoEvent,
    DemoSession,
    Notification,
    ScheduledMessage,
    SentMessage,
    TrackedLink,
    TrackingEvent,
    User,
)
from app.models.base import utcnow
from app.services import app_settings
from app.services.contacts import is_suppressed
from app.services.rendering import Rendered

logger = logging.getLogger("app.tracking")

DEMO_PARAM = "trc"
DEMO_SESSION_DAYS = 30
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# Links that must keep working exactly as written: unsubscribe/opt-out, sign-in, password and verification flows.
SENSITIVE = re.compile(
    r"unsubscribe|opt-?out|optout|email-preferences|/u/|/login|/log-in|/signin|/sign-in|/auth|oauth|password|"
    r"/reset|/verify|verification|confirm|magic|[?&](token|code|key|signature|sig)=",
    re.IGNORECASE,
)
# Security gateways, link scanners and HTTP libraries. Matching one flags a click; it never hides it.
SCANNER_UA = re.compile(
    r"bot\b|bot/|crawler|spider|slurp|preview|scanner|barracuda|proofpoint|mimecast|forcepoint|fireeye|trendmicro|"
    r"symantec|messagelabs|sophos|cisco|ironport|zscaler|safelinks|microsoft office|ms-office|msoffice|"
    r"python-requests|python-urllib|aiohttp|httpx|curl/|wget|go-http-client|java/|okhttp|libwww|headlesschrome|"
    r"phantomjs|electron/|facebookexternalhit|slackbot|linkexpanding|whatsapp|googleimageproxy|yahoomailproxy",
    re.IGNORECASE,
)
INTERACTIONS = ("application_started", "application_step", "signature_completed", "application_submitted",
                "form_submitted")
DEMO_FORMS = {"application", "reminders", "tip", "lookup", "payment", "checkin", "chat", "callback", "owner_lead"}
PIXEL_GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
HREF = re.compile(r'<a\b([^>]*?)\bhref="([^"]*)"([^>]*)>(.*?)</a>', re.IGNORECASE | re.DOTALL)
TEXT_URL = re.compile(r"https?://[^\s<>\"'()]+")


# --- settings -------------------------------------------------------------------------------------

def settings_for(db: Session) -> dict:
    return app_settings.get_section(db, "tracking")


def base_url() -> str:
    s = get_settings()
    return (s.tracking_base_url or s.public_url).rstrip("/")


def _norm(url: str) -> str:
    """scheme-less, lower-case host, no trailing slash or index.html: what a demo URL prefix is compared on."""
    parts = urlsplit(url.strip())
    path = re.sub(r"/index\.html?$", "", parts.path or "").rstrip("/")
    return f"{parts.netloc.lower()}{path}"


def is_demo_url(url: str, prefixes: list[str]) -> bool:
    target = _norm(url)
    for p in prefixes or []:
        prefix = _norm(p if "://" in p else f"https://{p}")
        if prefix and (target == prefix or target.startswith(prefix + "/")):
            return True
    return False


def should_track(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return False
    if SENSITIVE.search(url):
        return False
    own = urlsplit(base_url())
    public = urlsplit(get_settings().public_url)
    # never wrap our own tracking or unsubscribe pages
    return not (parts.netloc.lower() in (own.netloc.lower(), public.netloc.lower())
                and (parts.path.startswith("/t/") or parts.path.startswith("/u/")))


def _label(inner: str) -> str:
    text = re.sub(r"<[^>]+>", " ", inner)
    text = " ".join(html.unescape(text).split())
    if not text:
        alt = re.search(r'\balt="([^"]*)"', inner)
        text = html.unescape(alt.group(1)).strip() if alt else ""
    return text[:300]


def link_url(token: str) -> str:
    return f"{base_url()}/t/{token}"


def pixel_url(token: str) -> str:
    return f"{base_url()}/t/o/{token}.gif"


# --- 1. rewriting outgoing email ------------------------------------------------------------------

def apply_to_email(db: Session, rendered: Rendered, sm: ScheduledMessage, contact_id: uuid.UUID) -> Rendered:
    """Rewrite a rendered campaign email for one recipient. Safe to call again for the same scheduled message.

    Tracking must never stop an email: any failure (e.g. the migration not yet applied) rolls back to a savepoint and
    the email goes out exactly as rendered, untracked."""
    try:
        with db.begin_nested():
            return _apply(db, rendered, sm, contact_id)
    except Exception:
        logger.exception("link tracking skipped for scheduled message %s", sm.id)
        return rendered


def _apply(db: Session, rendered: Rendered, sm: ScheduledMessage, contact_id: uuid.UUID) -> Rendered:
    cfg = settings_for(db)
    if not cfg.get("click_tracking") and not cfg.get("open_tracking"):
        return rendered
    existing = {link.ordinal: link for link in db.scalars(
        select(TrackedLink).where(TrackedLink.scheduled_message_id == sm.id))}
    demo_prefixes = cfg.get("demo_urls") or []
    by_destination: dict[str, str] = {}
    ordinal = 0

    def token_for(n: int, kind: str, destination: str | None, label: str | None) -> str:
        link = existing.get(n)
        if link is None:
            link = TrackedLink(token=secrets.token_urlsafe(18), kind=kind, scheduled_message_id=sm.id,
                               contact_id=contact_id, campaign_id=sm.campaign_id, sequence_step_id=sm.sequence_step_id,
                               ordinal=n, destination_url=destination, label=label)
            db.add(link)
            existing[n] = link
        elif link.destination_url != destination:  # the email changed between attempts: follow what is sent now
            link.kind, link.destination_url, link.label = kind, destination, label
        return link.token

    def rewrite(m: re.Match) -> str:
        nonlocal ordinal
        href = html.unescape(m.group(2))
        if not should_track(href):
            return m.group(0)
        ordinal += 1
        kind = "demo" if is_demo_url(href, demo_prefixes) else "link"
        token = token_for(ordinal, kind, href, _label(m.group(4)))
        by_destination.setdefault(href, token)
        return f'<a{m.group(1)}href="{html.escape(link_url(token), quote=True)}"{m.group(3)}>{m.group(4)}</a>'

    out_html = HREF.sub(rewrite, rendered.html) if cfg.get("click_tracking") else rendered.html
    # plain-text part: the same destinations point at the same tokens
    out_text = TEXT_URL.sub(lambda m: link_url(by_destination[m.group(0)]) if m.group(0) in by_destination else m.group(0),
                            rendered.text)
    if cfg.get("open_tracking"):
        token = token_for(0, "open_pixel", None, None)
        pixel = (f'<img src="{html.escape(pixel_url(token), quote=True)}" width="1" height="1" alt="" border="0" '
                 'style="display:block;width:1px;height:1px;border:0;margin:0;padding:0">')
        out_html = out_html[:-6] + pixel + "</div>" if out_html.endswith("</div>") else out_html + pixel
    db.flush()
    return Rendered(subject=rendered.subject, text=out_text, html=out_html)


def attach_sent(db: Session, scheduled_message_id: uuid.UUID, sent_message_id: uuid.UUID) -> None:
    db.execute(update(TrackedLink).where(TrackedLink.scheduled_message_id == scheduled_message_id)
               .values(sent_message_id=sent_message_id))


# --- 2. clicks and opens --------------------------------------------------------------------------

@dataclass
class Hit:
    user_agent: str | None
    ip: str | None
    method: str = "GET"
    crm_user: bool = False  # a signed-in teammate (previewing a sent email in the CRM)


def ip_hash(ip: str | None) -> str | None:
    if not ip:
        return None
    return hmac.new(get_settings().secret_key.encode(), f"ip:{ip}".encode(), hashlib.sha256).hexdigest()[:32]


def _bot_reasons(db: Session, link: TrackedLink, hit: Hit, now: datetime, cfg: dict) -> list[str]:
    reasons = []
    ua = (hit.user_agent or "").strip()
    if not ua:
        reasons.append("no_user_agent")
    elif SCANNER_UA.search(ua):
        reasons.append("scanner_user_agent")
    if hit.method == "HEAD":
        reasons.append("head_request")
    if hit.crm_user:
        reasons.append("crm_user")
    sent_at = db.scalar(select(SentMessage.sent_at).where(SentMessage.id == link.sent_message_id)) \
        if link.sent_message_id else None
    window = int(cfg.get("scanner_window_seconds") or 0)
    if sent_at and window and (now - sent_at).total_seconds() < window:
        reasons.append("seconds_after_delivery")
    if link.kind != "open_pixel" and link.scheduled_message_id:
        # several different links of one email opened within 3 seconds: a gateway following every link
        burst = db.scalars(
            select(TrackingEvent).join(TrackedLink, TrackedLink.id == TrackingEvent.link_id)
            .where(TrackedLink.scheduled_message_id == link.scheduled_message_id, TrackedLink.id != link.id,
                   TrackingEvent.type == "click", TrackingEvent.occurred_at >= now - timedelta(seconds=3))).all()
        if burst:
            reasons.append("multiple_links_at_once")
            for e in burst:
                if not e.suspected_bot:
                    e.suspected_bot, e.bot_reasons = True, [*(e.bot_reasons or []), "multiple_links_at_once"]
    return reasons


def add_param(url: str, name: str, value: str) -> str:
    """Append one query parameter, keeping every existing one (and the fragment) exactly as it was."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    query.append((name, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _campaign_name(db: Session, campaign_id) -> str | None:
    return db.scalar(select(Campaign.name).where(Campaign.id == campaign_id)) if campaign_id else None


def _activity_exists(db: Session, contact_id, type_: str, key: str, value: str) -> bool:
    return db.scalar(select(CrmActivity.id).where(
        CrmActivity.contact_id == contact_id, CrmActivity.type == type_,
        CrmActivity.data[key].astext == value).limit(1)) is not None


def _log(db: Session, contact_id, type_: str, summary: str, at: datetime, **data) -> None:
    db.add(CrmActivity(contact_id=contact_id, type=type_, summary=summary, data=data, created_at=at))


def _log_click(db: Session, link: TrackedLink, event: TrackingEvent) -> None:
    """One timeline entry per tracked link (the first human click); the raw events keep every repeat."""
    if link.contact_id is None or _activity_exists(db, link.contact_id, "email_click", "link_id", str(link.id)):
        return
    what = link.label or link.destination_url or "a link"
    _log(db, link.contact_id, "email_click", f"Clicked “{what}”", event.occurred_at, link_id=str(link.id),
         kind=link.kind, url=link.destination_url, campaign_id=str(link.campaign_id) if link.campaign_id else None,
         campaign=_campaign_name(db, link.campaign_id))


def record_click(db: Session, token: str, hit: Hit) -> str | None:
    """The destination to redirect to (None for an unknown token). Never raises for a valid token: a person who
    clicked must always reach the page, even when the event itself is not recorded."""
    if not TOKEN_RE.match(token or ""):
        return None
    link = db.scalar(select(TrackedLink).where(TrackedLink.token == token, TrackedLink.kind != "open_pixel"))
    if link is None or not link.destination_url:
        return None
    destination = link.destination_url
    contact = db.get(Contact, link.contact_id) if link.contact_id else None
    if contact is None or is_suppressed(db, contact.email):  # opted out: redirect, but record nothing about them
        return destination
    cfg = settings_for(db)
    now = utcnow()
    reasons = _bot_reasons(db, link, hit, now, cfg)
    event = TrackingEvent(type="click", link_id=link.id, contact_id=link.contact_id, campaign_id=link.campaign_id,
                          occurred_at=now, ip_hash=ip_hash(hit.ip), user_agent=(hit.user_agent or "")[:300] or None,
                          method=hit.method[:8], suspected_bot=bool(reasons), bot_reasons=reasons)
    db.add(event)
    db.flush()
    if not reasons:
        _log_click(db, link, event)
    if link.kind == "demo" and hit.method != "HEAD" and "crm_user" not in reasons:
        session = DemoSession(token=secrets.token_urlsafe(24), link_id=link.id, click_event_id=event.id,
                              contact_id=link.contact_id, campaign_id=link.campaign_id, created_at=now,
                              expires_at=now + timedelta(days=DEMO_SESSION_DAYS), event_count=0)
        db.add(session)
        db.flush()
        destination = add_param(destination, DEMO_PARAM, session.token)
    return destination


def record_open(db: Session, token: str, hit: Hit) -> None:
    if not TOKEN_RE.match(token or ""):
        return
    link = db.scalar(select(TrackedLink).where(TrackedLink.token == token, TrackedLink.kind == "open_pixel"))
    if link is None or link.contact_id is None:
        return
    contact = db.get(Contact, link.contact_id)
    if contact is None or is_suppressed(db, contact.email):
        return
    now = utcnow()
    ua = (hit.user_agent or "")[:300] or None
    reasons = [r for r in _bot_reasons(db, link, hit, now, settings_for(db)) if r != "scanner_user_agent"]
    if ua and re.search(r"googleimageproxy|yahoomailproxy", ua, re.IGNORECASE):
        reasons.append("image_proxy")  # still an (estimated) open: these proxies fetch when the email is viewed
    db.add(TrackingEvent(type="open", link_id=link.id, contact_id=link.contact_id, campaign_id=link.campaign_id,
                         occurred_at=now, ip_hash=ip_hash(hit.ip), user_agent=ua, method=hit.method[:8],
                         suspected_bot=any(r != "image_proxy" for r in reasons), bot_reasons=reasons))


# --- 3. demo events -------------------------------------------------------------------------------

def _slug(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    s = re.sub(r"[^a-z0-9_-]", "", value.lower())[:limit]
    return s or None


def allowed_origin(db: Session, origin: str | None) -> bool:
    if not origin:
        return False
    host = urlsplit(origin).netloc.lower()
    return any(urlsplit(o if "://" in o else f"https://{o}").netloc.lower() == host
               for o in settings_for(db).get("demo_origins") or [])


def record_demo_event(db: Session, payload: dict, user_agent: str | None) -> bool:
    """Store one demo step for an attributed session. Returns False (and stores nothing) for anything unknown,
    expired or malformed. Only the fields below are read; anything else in the payload is ignored."""
    token, kind = payload.get("t"), payload.get("e")
    if not isinstance(token, str) or not TOKEN_RE.match(token) or kind not in (
            "page_view", "application_started", "application_step", "signature_completed",
            "application_submitted", "form_submitted"):
        return False
    session = db.scalar(select(DemoSession).where(DemoSession.token == token).with_for_update())
    now = utcnow()
    if session is None or session.expires_at < now:
        return False
    if session.event_count >= 500:  # a session that is clearly being replayed stops counting
        return False
    form = _slug(payload.get("form"), 30)
    if kind == "application_submitted":
        form = "application"
    if form and form not in DEMO_FORMS:
        form = "other"
    step = payload.get("step")
    step = step if isinstance(step, int) and not isinstance(step, bool) and 0 <= step <= 20 else None
    flag = lambda k: payload.get(k) if isinstance(payload.get(k), bool) else None  # noqa: E731
    event = DemoEvent(demo_session_id=session.id, contact_id=session.contact_id, campaign_id=session.campaign_id,
                      type=kind, page=_slug(payload.get("page"), 40), form=form, step=step, sample=flag("sample"),
                      signed=flag("signed"), user_agent=(user_agent or "")[:300] or None, occurred_at=now)
    db.add(event)
    session.first_event_at = session.first_event_at or now
    session.last_event_at = now
    session.event_count += 1
    if session.contact_id:
        _demo_activity(db, session, event)
    if kind in INTERACTIONS:
        _verify_click(db, session)
    db.flush()
    return True


def _verify_click(db: Session, session: DemoSession) -> None:
    """A person filled in the demo: the click that brought them was human, whatever it looked like at the time."""
    if not session.click_event_id:
        return
    click = db.get(TrackingEvent, session.click_event_id)
    if click is None or click.verified_human:
        return
    click.verified_human = True
    if "crm_user" not in (click.bot_reasons or []):
        _log_click(db, db.get(TrackedLink, session.link_id), click)


def _demo_activity(db: Session, session: DemoSession, event: DemoEvent) -> None:
    sid = str(session.id)
    campaign = _campaign_name(db, session.campaign_id)
    common = {"demo_session_id": sid, "campaign_id": str(session.campaign_id) if session.campaign_id else None,
              "campaign": campaign}
    if event.type == "page_view" and not _activity_exists(db, session.contact_id, "demo_visit", "demo_session_id", sid):
        _log(db, session.contact_id, "demo_visit", "Visited the demo", event.occurred_at, **common)
    elif event.type in ("application_started", "application_step") and not _activity_exists(
            db, session.contact_id, "demo_application_started", "demo_session_id", sid):
        _log(db, session.contact_id, "demo_application_started", "Started Bail Application Demo", event.occurred_at,
             **common)
    elif event.type == "application_submitted" and not _activity_exists(
            db, session.contact_id, "demo_application_completed", "demo_session_id", sid):
        summary = "Completed Bail Application Demo" + (" — Signed Electronically" if event.signed else "")
        _log(db, session.contact_id, "demo_application_completed", summary, event.occurred_at, signed=event.signed,
             sample=event.sample, **common)
        _notify_completion(db, session.contact_id, summary)
    elif event.type == "form_submitted" and event.form and not _activity_exists(
            db, session.contact_id, "demo_form", "demo_form_key", f"{sid}:{event.form}"):
        _log(db, session.contact_id, "demo_form", f"Tried the demo's {event.form.replace('_', ' ')} feature",
             event.occurred_at, demo_form_key=f"{sid}:{event.form}", form=event.form, **common)


def _notify_completion(db: Session, contact_id, summary: str) -> None:
    """In-app notice to the opportunity owner, or to every active admin when nobody owns it."""
    contact = db.get(Contact, contact_id)
    owner = db.scalar(select(CrmOpportunity.owner_id).where(CrmOpportunity.contact_id == contact_id))
    users = [owner] if owner else list(db.scalars(select(User.id).where(
        User.role == "admin", User.is_active, User.deleted_at.is_(None))))
    who = " ".join(p for p in (contact.first_name, contact.last_name) if p) or contact.email
    now = utcnow()
    for uid in users:
        db.add(Notification(user_id=uid, kind="demo_completed", title=f"{who}: {summary}",
                            body=contact.company_name or contact.email, contact_id=contact_id, fire_at=now,
                            data={"tab": "engagement"}))


# --- retention ------------------------------------------------------------------------------------

def purge(db: Session, now: datetime | None = None) -> dict:
    """Raw tracking and demo events older than the retention period are deleted. Timeline entries in crm_activities
    (one line per click/visit/completion, no user agents or IP hashes) stay with the prospect."""
    days = int(settings_for(db).get("retention_days") or 0)
    if days <= 0:
        return {}
    cutoff = (now or utcnow()) - timedelta(days=days)
    out = {
        "tracking_events": db.execute(delete(TrackingEvent).where(TrackingEvent.occurred_at < cutoff)).rowcount,
        "demo_events": db.execute(delete(DemoEvent).where(DemoEvent.occurred_at < cutoff)).rowcount,
        "demo_sessions": db.execute(delete(DemoSession).where(DemoSession.expires_at < cutoff)).rowcount,
    }
    return {k: v for k, v in out.items() if v}


def human_click_filter():
    return (TrackingEvent.type == "click") & ((TrackingEvent.suspected_bot.is_(False)) | TrackingEvent.verified_human)


def count_human_clicks(db: Session, contact_id) -> int:
    return db.scalar(select(func.count()).select_from(TrackingEvent).where(
        TrackingEvent.contact_id == contact_id, human_click_filter())) or 0
