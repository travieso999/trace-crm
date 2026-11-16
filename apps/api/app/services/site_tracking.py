"""Website tracking: what trace-studio.tech forwards about prospects (its spec: tracking-events.md).

The website records each step in the browser, batches them through its own ``/api/demo-events`` and forwards every
batch server to server to ``POST /api/track/site-events`` with ``Authorization: Bearer <TRACKING_WEBHOOK_SECRET>``.

Matching a batch to a prospect, in order:
1. ``visitor.rid``: the prospect id from the campaign link (``?rid={{recipient_id}}``), i.e. ``contacts.id``;
2. the visitor (``vid``) was matched before;
3. an ``identify`` email or phone that matches exactly one prospect.
Unmatched batches are stored under the visitor and linked (with everything before) as soon as one of these matches.

Rules: only the keys the spec lists per event type are kept; an event carrying card, bank, PIN or password content is
dropped whole and never stored; every event has a content hash (``dedupe_key``), so a retried batch adds nothing.
"""

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    CampaignContact,
    Contact,
    CrmActivity,
    CrmOpportunity,
    Notification,
    SentMessage,
    SiteEvent,
    SiteVisitor,
    TrackedLink,
    TrackingEvent,
    User,
)
from app.models.base import utcnow
from app.services.normalize import normalize_email

logger = logging.getLogger("app.site_tracking")

MAX_EVENTS = 60
MAX_BODY = 512 * 1024
ENDPOINT_PATH = "/api/track/site-events"

# The spec's event types and the data keys each may carry. Anything else in ``data`` is dropped.
EVENT_FIELDS: dict[str, tuple[str, ...]] = {
    "session_start": ("landing", "referrer", "recipient"),
    "page_view": ("title", "url"),
    "page_leave": ("active_seconds", "total_seconds", "max_scroll"),
    "tab_hidden": (),
    "tab_visible": (),
    "click": ("kind", "label", "href"),
    "video_play": ("video", "play", "at_second", "watched_seconds"),
    "video_pause": ("at_second", "watched_seconds"),
    "video_progress": ("percent", "watched_seconds"),
    "video_complete": ("watched_seconds",),
    "form_start": ("form",),
    "field_done": ("form", "field", "filled", "sensitive"),
    "form_step": ("form", "step", "name"),
    "form_error": ("step", "field"),
    "form_submit": ("form",),
    "demo_form": ("kind",),
    "application_completed": ("reference",),
    "identify": ("name", "email", "phone"),
    "callback_request": ("agency",),
    "contact_panel": ("action",),
}
NUMERIC = {"active_seconds", "total_seconds", "max_scroll", "play", "at_second", "watched_seconds", "percent", "step"}
BOOLEAN = {"filled", "sensitive"}
CONTACT_TAPS = ("call", "text", "email", "booking")
# events that show a person working with the demo or reaching out (High), beyond looking
INTERACTIONS = ("form_start", "form_step", "form_error", "form_submit", "demo_form", "application_completed",
                "identify", "callback_request")
WATCHED_PERCENTS = tuple(str(p) for p in range(75, 101))

# Card, bank, PIN and password content. A key like these (with a value) or a card-number-like value drops the event.
FORBIDDEN_KEY = re.compile(
    r"card|cvv|cvc|\bcsc\b|expiry|expiration|iban|routing|swift|bank|acct|account_?(number|no|num)|"
    r"(^|_)pin($|_|code|number)|passcode|password|passwd|pwd|ssn|social_security", re.IGNORECASE)
CARDLIKE = re.compile(r"^[\d][\d \-.]{11,24}\d$")


class Rejected(ValueError):
    """The batch as a whole doesn't match the spec (422)."""


# --- payload shape --------------------------------------------------------------------------------

def _short(limit: int):
    def check(v):
        if v is None:
            return None
        if not isinstance(v, str | int | float) or isinstance(v, bool):
            raise ValueError("must be text")
        v = str(v).strip()
        return v[:limit] or None
    return check


class VisitorIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    vid: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    sid: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    rid: str | None = None
    name: str | None = None
    email: str | None = None
    phone: str | None = None

    _rid = field_validator("rid", mode="before")(_short(100))
    _name = field_validator("name", mode="before")(_short(200))
    _email = field_validator("email", mode="before")(_short(320))
    _phone = field_validator("phone", mode="before")(_short(60))


class GeoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    city: str | None = None
    region: str | None = None
    country: str | None = None

    _city = field_validator("city", "region", mode="before")(_short(120))
    _country = field_validator("country", mode="before")(_short(8))


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    source: str | None = Field(None, max_length=100)
    received_at: datetime | None = None
    visitor: VisitorIn
    geo: GeoIn | None = None
    user_agent: str | None = None
    ctx: dict | None = None
    events: list[Any] = Field(max_length=MAX_EVENTS)

    _ua = field_validator("user_agent", mode="before")(_short(300))


class EventIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str = Field(min_length=1, max_length=40)
    at: datetime | None = None
    site: str | None = None
    page: str | None = None
    data: dict = Field(default_factory=dict)

    _site = field_validator("site", mode="before")(_short(80))
    _page = field_validator("page", mode="before")(_short(300))

    @field_validator("data", mode="before")
    @classmethod
    def _data(cls, v):
        return v if isinstance(v, dict) else {}


def parse(body: bytes) -> BatchIn:
    if len(body) > MAX_BODY:
        raise Rejected("Batch too large")
    try:
        raw = json.loads(body or b"null")
    except ValueError as exc:
        raise Rejected("Body is not JSON") from exc
    if not isinstance(raw, dict):
        raise Rejected("Body must be a JSON object")
    try:
        return BatchIn.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise Rejected(f"{where}: {first.get('msg')}") from exc


# --- privacy guard ---------------------------------------------------------------------------------

def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def looks_like_card(value: str) -> bool:
    if not CARDLIKE.match(value.strip()):
        return False
    digits = re.sub(r"\D", "", value)
    return 13 <= len(digits) <= 19 and _luhn(digits)


def forbidden(obj: Any, depth: int = 0) -> bool:
    """True when ``obj`` carries card, bank, PIN or password content: a key named like one with a value, or a value
    that looks like a card number. Labels (``field: "Card number"``) and ``sensitive: true`` are fine."""
    if depth > 6:
        return True
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and FORBIDDEN_KEY.search(k) and v not in (None, "", False):
                return True
            if forbidden(v, depth + 1):
                return True
        return False
    if isinstance(obj, list):
        return any(forbidden(v, depth + 1) for v in obj)
    if isinstance(obj, str):
        return looks_like_card(obj)
    return False


def _clean_data(kind: str, data: dict) -> dict:
    out: dict = {}
    for key in EVENT_FIELDS[kind]:
        v = data.get(key)
        if v is None:
            continue
        if key in BOOLEAN:
            if isinstance(v, bool):
                out[key] = v
        elif key in NUMERIC:
            if isinstance(v, int | float) and not isinstance(v, bool) and 0 <= v <= 10_000_000:
                out[key] = int(round(v))
        elif isinstance(v, str | int | float) and not isinstance(v, bool):
            s = str(v).strip()[:500]
            if s:
                out[key] = s
    if kind == "click" and out.get("kind"):
        out["kind"] = out["kind"].lower()[:20]
    if kind == "identify" and out.get("email"):
        out["email"] = out["email"].lower()
    return out


# --- device -----------------------------------------------------------------------------------------

def device(ua: str | None) -> str | None:
    """'iPhone · Safari', 'Windows · Chrome', … from a user agent. Never stored in full outside raw events."""
    if not ua:
        return None
    os_ = next((name for pat, name in (
        (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"), (r"CrOS", "ChromeOS"),
        (r"Windows", "Windows"), (r"Macintosh|Mac OS X", "Mac"), (r"Linux", "Linux")) if re.search(pat, ua)), None)
    browser = next((name for pat, name in (
        (r"FBAN|FBAV", "Facebook app"), (r"Instagram", "Instagram app"), (r"LinkedInApp", "LinkedIn app"),
        (r"\bGSA/", "Google app"), (r"Edg/|EdgiOS|EdgA", "Edge"), (r"OPR/|Opera", "Opera"),
        (r"SamsungBrowser", "Samsung Internet"), (r"Firefox|FxiOS", "Firefox"), (r"Chrome|CriOS", "Chrome"),
        (r"Safari", "Safari")) if re.search(pat, ua)), None)
    return " · ".join(p for p in (os_, browser) if p) or "Unknown device"


# --- matching ---------------------------------------------------------------------------------------

def _live(c: Contact | None) -> Contact | None:
    return c if c is not None and c.deleted_at is None else None


def contact_for_rid(db: Session, rid: str | None) -> Contact | None:
    """``rid`` is a prospect id (contacts.id). A campaign-recipient id (campaign_contacts.id) is accepted too."""
    if not rid:
        return None
    try:
        key = uuid.UUID(rid.strip())
    except ValueError:
        return None
    if c := _live(db.get(Contact, key)):
        return c
    cc = db.get(CampaignContact, key)
    return _live(db.get(Contact, cc.contact_id)) if cc else None


def _digits(phone: str | None) -> str:
    d = re.sub(r"\D", "", phone or "")
    return d[-10:] if len(d) >= 10 else ""


def contact_for_identity(db: Session, email: str | None, phone: str | None) -> Contact | None:
    """Exactly one live prospect with this email, else exactly one with this phone (last 10 digits)."""
    if email:
        try:
            e = normalize_email(email)
        except Exception:
            e = None
        if e and (c := db.scalar(select(Contact).where(Contact.email == e, Contact.deleted_at.is_(None)))):
            return c
    digits = _digits(phone)
    if digits:
        matches = db.scalars(select(Contact).where(
            Contact.deleted_at.is_(None), Contact.phone.is_not(None),
            func.right(func.regexp_replace(Contact.phone, r"\D", "", "g"), 10) == digits).limit(2)).all()
        if len(matches) == 1:
            return matches[0]
    return None


def campaign_for(db: Session, contact_id: uuid.UUID) -> uuid.UUID | None:
    """The campaign that most likely sent them: the last demo link they clicked, else the last campaign email."""
    cid = db.scalar(select(TrackedLink.campaign_id).join(TrackingEvent, TrackingEvent.link_id == TrackedLink.id)
                    .where(TrackedLink.contact_id == contact_id, TrackedLink.kind == "demo",
                           TrackedLink.campaign_id.is_not(None))
                    .order_by(TrackingEvent.occurred_at.desc()).limit(1))
    return cid or db.scalar(select(SentMessage.campaign_id).where(
        SentMessage.contact_id == contact_id, SentMessage.kind == "sequence", SentMessage.campaign_id.is_not(None))
        .order_by(SentMessage.sent_at.desc()).limit(1))


def _visitor(db: Session, vid: str, now: datetime) -> SiteVisitor:
    db.execute(insert(SiteVisitor).values(id=uuid.uuid4(), vid=vid, first_seen_at=now, last_seen_at=now, event_count=0)
               .on_conflict_do_nothing(index_elements=["vid"]))
    return db.scalar(select(SiteVisitor).where(SiteVisitor.vid == vid).with_for_update())


# --- ingest -----------------------------------------------------------------------------------------

FUTURE_SLACK = timedelta(days=1)


def _utc(at: datetime | None) -> datetime | None:
    return at.replace(tzinfo=UTC) if at is not None and at.tzinfo is None else at


def _dedupe_key(vid: str, sid: str, kind: str, at: datetime, site, page, data: dict) -> str:
    raw = json.dumps([vid, sid, kind, at.isoformat(), site, page, data], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def ingest(db: Session, batch: BatchIn, now: datetime | None = None) -> dict:
    now = now or utcnow()
    v = batch.visitor
    geo = batch.geo or GeoIn()
    dev = device(batch.user_agent)
    # the website server's clock; a browser clock more than a day ahead of it is not believed
    received = _utc(batch.received_at)
    received = received if received is not None and received <= now + FUTURE_SLACK else now
    result = {"received": len(batch.events), "stored": 0, "duplicates": 0, "rejected": 0, "skipped": 0}

    rows: list[dict] = []
    identities: list[dict] = []
    for raw in batch.events:
        if not isinstance(raw, dict):
            result["skipped"] += 1
            continue
        if forbidden(raw):
            result["rejected"] += 1  # never stored, not even the type
            continue
        try:
            ev = EventIn.model_validate(raw)
        except ValidationError:
            result["skipped"] += 1
            continue
        if ev.type not in EVENT_FIELDS:
            result["skipped"] += 1
            continue
        data = _clean_data(ev.type, ev.data)
        reported = _utc(ev.at) or received
        at = reported if reported <= received + FUTURE_SLACK else received
        if ev.type == "identify":
            identities.append(data)
        # keyed on what the website reported, so a retry hashes the same whatever the clocks did
        rows.append({"type": ev.type, "site": ev.site, "page": ev.page, "data": data, "occurred_at": at,
                     "dedupe_key": _dedupe_key(v.vid, v.sid, ev.type, reported, ev.site, ev.page, data)})
    if result["rejected"]:
        logger.warning("site tracking: dropped %s event(s) carrying payment or password content (vid %s)",
                       result["rejected"], v.vid)

    visitor = _visitor(db, v.vid, now)
    contact = contact_for_rid(db, v.rid)
    if contact is None and visitor.contact_id:
        contact = _live(db.get(Contact, visitor.contact_id))
    # details typed into the website: the visitor block repeats them; identify events carry them first-hand
    email = next((i["email"] for i in reversed(identities) if i.get("email")), None) or v.email
    phone = next((i["phone"] for i in reversed(identities) if i.get("phone")), None) or v.phone
    name = next((i["name"] for i in reversed(identities) if i.get("name")), None) or v.name
    if contact is None and (email or phone):
        contact = contact_for_identity(db, email, phone)
    newly_linked = contact is not None and visitor.contact_id != contact.id

    visitor.rid = v.rid or visitor.rid
    visitor.name, visitor.email, visitor.phone = name or visitor.name, (email or visitor.email or None), phone or visitor.phone
    if visitor.email:
        visitor.email = visitor.email.lower()[:320]
    visitor.city, visitor.region, visitor.country = geo.city or visitor.city, geo.region or visitor.region, \
        geo.country or visitor.country
    visitor.device = dev or visitor.device
    visitor.last_seen_at = max(visitor.last_seen_at, max((r["occurred_at"] for r in rows), default=now))
    if contact is not None:
        visitor.contact_id = contact.id
    campaign_id = campaign_for(db, contact.id) if contact is not None else None

    inserted: list[uuid.UUID] = []
    if rows:
        for r in rows:
            r.update(id=uuid.uuid4(), visitor_id=visitor.id, sid=v.sid, rid=v.rid,
                     contact_id=contact.id if contact else None, campaign_id=campaign_id, city=geo.city,
                     region=geo.region, country=geo.country, device=dev, user_agent=batch.user_agent, received_at=now)
        inserted = list(db.scalars(insert(SiteEvent).values(rows)
                                   .on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(SiteEvent.id)))
    result["stored"] = len(inserted)
    result["duplicates"] = len(rows) - len(inserted)
    visitor.event_count += len(inserted)

    backfilled: list[uuid.UUID] = []
    if newly_linked:
        # everything this browser did before we knew who it was now belongs to the prospect
        backfilled = list(db.scalars(update(SiteEvent).where(
            SiteEvent.visitor_id == visitor.id, SiteEvent.contact_id.is_(None))
            .values(contact_id=contact.id, campaign_id=campaign_id).returning(SiteEvent.id)))
    events = db.scalars(select(SiteEvent).where(SiteEvent.id.in_(set(inserted) | set(backfilled)))
                        .order_by(SiteEvent.occurred_at)).all() if (inserted or backfilled) else []
    fresh = set(inserted)
    for ev in events:
        if contact is not None:
            _milestone(db, contact, ev)
            if ev.type == "identify" and ev.id in fresh:
                apply_identify(db, contact, ev)
        if ev.type == "callback_request" and ev.id in fresh:
            notify_callback(db, contact, visitor, ev)
        if ev.type == "application_completed" and ev.id in fresh and contact is not None:
            _notify_completed(db, contact, ev)
    db.flush()
    result["matched"] = contact is not None
    result["contact_id"] = str(contact.id) if contact else None
    return result


# --- prospect details from identify ---------------------------------------------------------------------

def _split_name(name: str) -> tuple[str, str | None]:
    parts = name.split()
    return (parts[0][:120], " ".join(parts[1:])[:120] or None) if parts else ("", None)


def apply_identify(db: Session, contact: Contact, ev: SiteEvent) -> None:
    """Fill in or update the prospect's name and phone from what they typed about themselves. A different email is
    kept beside the prospect's (custom field ``website_email``): replacing the address campaigns send to would
    redirect the sequence and could clash with another prospect's address."""
    name, phone, email = ev.data.get("name"), ev.data.get("phone"), ev.data.get("email")
    changes: dict[str, list] = {}
    if name:
        first, last = _split_name(name)
        current = " ".join(p for p in (contact.first_name, contact.last_name) if p)
        # "Pat" typed for a prospect called Pat Owner is the same person, not a rename
        same_first_only = last is None and first.lower() == (contact.first_name or "").lower()
        if first and " ".join(name.split()).lower() != current.lower() and not same_first_only:
            new_last = last if last is not None else contact.last_name
            changes["name"] = [current or None, " ".join(p for p in (first, new_last) if p)]
            contact.first_name, contact.last_name = first, new_last
    if phone and _digits(phone) != _digits(contact.phone) and phone != contact.phone:
        changes["phone"] = [contact.phone, phone[:60]]
        contact.phone = phone[:60]
    if email:
        try:
            email = normalize_email(email)
        except Exception:
            email = email.lower()
        custom = dict(contact.custom_fields or {})
        if email != contact.email and custom.get("website_email") != email:
            changes["website_email"] = [custom.get("website_email"), email]
            custom["website_email"] = email
            contact.custom_fields = custom
    if not changes:
        return
    labels = {"name": "name", "phone": "phone", "website_email": "email"}
    summary = "Updated from the website: " + ", ".join(
        f"{labels[k]} {old + ' → ' if old else ''}{new}" for k, (old, new) in changes.items())
    db.add(CrmActivity(contact_id=contact.id, type="site_identify", summary=summary[:2000], created_at=ev.occurred_at,
                       data={"site_key": f"identify:{ev.id}", "changes": changes}))


# --- CRM timeline milestones and alerts -------------------------------------------------------------

def _exists(db: Session, contact_id, key: str) -> bool:
    return db.scalar(select(CrmActivity.id).where(
        CrmActivity.contact_id == contact_id, CrmActivity.type.like("site_%"),
        CrmActivity.data["site_key"].astext == key).limit(1)) is not None


def _once(db: Session, contact: Contact, ev: SiteEvent, key: str, type_: str, summary: str, **data) -> None:
    if _exists(db, contact.id, key):
        return
    db.add(CrmActivity(contact_id=contact.id, type=type_, summary=summary, created_at=ev.occurred_at,
                       data={"site_key": key, "sid": ev.sid, "site": ev.site, "page": ev.page,
                             "campaign_id": str(ev.campaign_id) if ev.campaign_id else None, **data}))


def _milestone(db: Session, contact: Contact, ev: SiteEvent) -> None:
    """One CRM activity line per meaningful step (and per website visit); the full detail stays in site_events."""
    d, sid = ev.data or {}, ev.sid
    if ev.type in ("session_start", "page_view"):
        _once(db, contact, ev, f"visit:{sid}", "site_visit", f"Visited {ev.site or 'trace-studio.tech'}")
    if ev.type == "page_view" and (ev.page or "").startswith("/demos/"):
        _once(db, contact, ev, f"demo:{sid}", "site_demo_opened", f"Opened the live demo ({ev.site or ev.page})")
    elif ev.type == "video_complete" or (ev.type == "video_progress" and str(d.get("percent")) in WATCHED_PERCENTS):
        what = "Watched the whole video" if ev.type == "video_complete" else f"Watched {d.get('percent')}% of the video"
        _once(db, contact, ev, f"video:{sid}", "site_video_watched", what)
    elif ev.type == "form_step" or (ev.type == "form_start" and d.get("form") == "bail_application"):
        _once(db, contact, ev, f"app_start:{sid}", "site_application_started", "Started the bail application")
    elif ev.type == "application_completed":
        _once(db, contact, ev, f"app_done:{sid}", "site_application_completed",
              "Completed the application" + (f" (ref {d['reference']})" if d.get("reference") else ""))
    elif ev.type == "callback_request":
        _once(db, contact, ev, f"callback:{ev.id}", "site_callback_request",
              "Asked for a call back" + (f" ({d['agency']})" if d.get("agency") else ""))
    elif ev.type == "click" and d.get("kind") in CONTACT_TAPS:
        _once(db, contact, ev, f"tap:{sid}:{d['kind']}", "site_contact_tap", describe(ev) or "Tapped a contact button")
    elif ev.type == "demo_form" and d.get("kind"):
        _once(db, contact, ev, f"form:{sid}:{d['kind']}", "site_demo_form", describe(ev) or "Used a demo form")


def _recipients(db: Session, contact: Contact | None) -> set[uuid.UUID]:
    users = set(db.scalars(select(User.id).where(User.role == "admin", User.is_active.is_(True), User.deleted_at.is_(None))))
    if contact is not None:
        owner = db.scalar(select(CrmOpportunity.owner_id).where(CrmOpportunity.contact_id == contact.id))
        if owner and db.scalar(select(User.id).where(User.id == owner, User.is_active.is_(True))):
            users.add(owner)
    return users


def _who(contact: Contact | None, visitor: SiteVisitor | None = None) -> str:
    if contact is not None:
        return " ".join(p for p in (contact.first_name, contact.last_name) if p) or contact.email
    return (visitor.name or visitor.email or visitor.phone or "A website visitor") if visitor else "A website visitor"


def notify_callback(db: Session, contact: Contact | None, visitor: SiteVisitor, ev: SiteEvent) -> None:
    """The hottest signal: a bell notice for every admin and the prospect's owner, even when the visitor is unknown."""
    phone = visitor.phone or (contact.phone if contact else None)
    details = [p for p in (phone, contact.company_name if contact else None, (ev.data or {}).get("agency"),
                           ", ".join(p for p in (ev.city, ev.region) if p)) if p]
    for uid in _recipients(db, contact):
        db.add(Notification(user_id=uid, kind="callback_request", title=f"Call back requested: {_who(contact, visitor)}"[:300],
                            body=" · ".join(details) or None, contact_id=contact.id if contact else None,
                            fire_at=ev.occurred_at, data={"tab": "engagement", "phone": phone}))


def _notify_completed(db: Session, contact: Contact, ev: SiteEvent) -> None:
    owner = db.scalar(select(CrmOpportunity.owner_id).where(CrmOpportunity.contact_id == contact.id))
    users = {owner} if owner else _recipients(db, None)
    for uid in users:
        db.add(Notification(user_id=uid, kind="demo_completed", title=f"{_who(contact)}: Completed the application"[:300],
                            body=contact.company_name or contact.email, contact_id=contact.id, fire_at=ev.occurred_at,
                            data={"tab": "engagement"}))


# --- readable timeline --------------------------------------------------------------------------------

DEMO_FORM_LABELS = {"application": "bail application", "tip": "tip", "checkin": "check-in", "reminders": "court reminders",
                    "lookup": "lookup", "payment": "payment", "chat": "chat", "text": "text message",
                    "callback": "call-back request"}
TAP_LABELS = {"call": "Tapped Call", "text": "Tapped Text", "email": "Tapped Email", "booking": "Opened Book a call"}
STRONG_TYPES = {"application_completed", "callback_request", "identify"}


def _mmss(seconds) -> str:
    if not isinstance(seconds, int):
        return ""
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def describe(ev: SiteEvent, state: dict | None = None) -> str | None:
    """Plain words for one event, or None for events folded into another line (page_leave, tab switches, …)."""
    d, state = ev.data or {}, state if state is not None else {}
    t, page = ev.type, ev.page or ""
    label = (d.get("label") or "").strip()
    if t == "session_start":
        return "Arrived from the campaign email" if (d.get("recipient") or "rid=" in (d.get("landing") or "")) \
            else "Started a visit" + (f" (from {d['referrer']})" if d.get("referrer") else "")
    if t == "page_view":
        if page.rstrip("/") == "/demo":
            return "Opened the demo video page"
        if page.startswith("/demos/") and not state.get(f"demo:{ev.site}"):
            state[f"demo:{ev.site}"] = True
            return f"Opened the live demo ({ev.site})" if ev.site else "Opened the live demo"
        return f"Viewed {d.get('title') or page or 'a page'}"
    if t == "click":
        kind = d.get("kind")
        if kind in TAP_LABELS:
            return TAP_LABELS[kind]
        if kind == "demo":
            return f"Tapped “{label}” to open the live demo" if label else "Tapped through to the live demo"
        if kind == "contact_open":
            return "Opened the contact options"
        if kind == "external":
            return f"Opened an outside link: {label or d.get('href') or 'link'}"
        return f"Clicked “{label}”" if label else f"Clicked {d.get('href') or 'a link'}"
    if t == "video_play":
        if state.get("played"):
            return None
        state["played"] = True
        return "Started the video" + (f" at {_mmss(d.get('at_second'))}" if d.get("at_second") else "")
    if t == "video_progress":
        pct = d.get("percent")
        return f"Watched {pct}% of the video" if isinstance(pct, int) and pct >= 25 else None
    if t == "video_complete":
        return "Watched the whole video"
    if t == "form_start":
        form = d.get("form") or "a"
        return None if form == "bail_application" else f"Started filling in the {form.replace('_', ' ')} form"
    if t == "form_step":
        step, name = d.get("step"), d.get("name")
        where = f"step {step}" + (f": {name}" if name else "") if step is not None else (name or "")
        if not state.get("app_started"):
            state["app_started"] = True
            return f"Started the bail application ({where})" if where else "Started the bail application"
        return f"Continued the bail application ({where})" if where else "Continued the bail application"
    if t == "form_error":
        field = (d.get("field") or "detail").lower()
        return f"Entered a fake {field}, was asked for a real one"
    if t == "form_submit":
        form = d.get("form") or ""
        return None if form == "bail_application" else f"Sent the {form.replace('_', ' ') or 'website'} form"
    if t == "demo_form":
        kind = d.get("kind") or "form"
        return f"Sent a test {DEMO_FORM_LABELS.get(kind, kind)} through the demo"
    if t == "application_completed":
        return "Completed the application" + (f" (ref {d['reference']})" if d.get("reference") else "")
    if t == "identify":
        given = [x for x in (d.get("name"), d.get("email"), d.get("phone")) if x]
        return "Gave their details" + (f": {' · '.join(given)}" if given else "")
    if t == "callback_request":
        return "Asked for a call back" + (f" ({d['agency']})" if d.get("agency") else "")
    if t == "contact_panel":
        return "Opened the Trace Studio contact panel" + (f" ({d['action']})" if d.get("action") else "")
    return None  # page_leave, tab_hidden, tab_visible, video_pause, field_done


def _strong(ev: SiteEvent) -> bool:
    d = ev.data or {}
    return ev.type in STRONG_TYPES or ev.type == "video_complete" or (ev.type == "click" and d.get("kind") in CONTACT_TAPS) \
        or (ev.type == "video_progress" and str(d.get("percent")) in WATCHED_PERCENTS)


def visits(db: Session, contact_id: uuid.UUID, *, limit: int = 2000) -> list[dict]:
    """The prospect's website visits, latest first; within a visit every step in the order it happened, with the page,
    time spent, place and device."""
    events = db.scalars(select(SiteEvent).where(SiteEvent.contact_id == contact_id)
                        .order_by(SiteEvent.occurred_at.desc()).limit(limit)).all()
    by_sid: dict[str, list[SiteEvent]] = {}
    for ev in reversed(events):
        by_sid.setdefault(ev.sid, []).append(ev)
    out = []
    for sid, evs in by_sid.items():
        state: dict = {}
        steps: list[dict] = []
        last_view: dict[str, dict] = {}
        for ev in evs:
            if ev.type == "page_leave":
                step = last_view.get(ev.page or "")
                if step is not None:
                    step["seconds"] = (ev.data or {}).get("active_seconds")
                    step["scroll"] = (ev.data or {}).get("max_scroll")
                continue
            text = describe(ev, state)
            if text is None:
                continue
            step = {"at": ev.occurred_at.isoformat(), "type": ev.type, "text": text, "site": ev.site, "page": ev.page,
                    "strong": _strong(ev), "seconds": None, "scroll": None}
            if ev.type == "page_view":
                last_view[ev.page or ""] = step
            steps.append(step)
        first, last = evs[0], evs[-1]
        active = sum((e.data or {}).get("active_seconds") or 0 for e in evs if e.type == "page_leave")
        out.append({
            "sid": sid, "started_at": first.occurred_at.isoformat(), "ended_at": last.occurred_at.isoformat(),
            "sites": list(dict.fromkeys(e.site for e in evs if e.site)),
            "city": next((e.city for e in evs if e.city), None), "region": next((e.region for e in evs if e.region), None),
            "country": next((e.country for e in evs if e.country), None),
            "device": next((e.device for e in evs if e.device), None), "active_seconds": active or None,
            "steps": steps, "events": len(evs),
        })
    out.sort(key=lambda x: x["started_at"], reverse=True)
    return out


# --- settings view ------------------------------------------------------------------------------------

def endpoint_url() -> str:
    s = get_settings()
    return f"{(s.tracking_base_url or s.public_url).rstrip('/')}{ENDPOINT_PATH}"


def status(db: Session, *, include_secret: bool) -> dict:
    s = get_settings()
    last = db.scalar(select(func.max(SiteEvent.received_at)))
    totals = db.execute(select(func.count(SiteVisitor.id), func.count(SiteVisitor.contact_id))).one()
    unmatched = db.scalars(select(SiteVisitor).where(SiteVisitor.contact_id.is_(None))
                           .order_by(SiteVisitor.last_seen_at.desc()).limit(10)).all()
    return {
        "endpoint": endpoint_url(),
        "configured": bool(s.tracking_webhook_secret),
        "secret": s.tracking_webhook_secret if include_secret else None,
        "last_received_at": last.isoformat() if last else None,
        "events": db.scalar(select(func.count(SiteEvent.id))) or 0,
        "visitors": totals[0], "matched_visitors": totals[1],
        "unmatched": [{"vid": u.vid, "rid": u.rid, "name": u.name, "email": u.email, "phone": u.phone,
                       "city": u.city, "region": u.region, "device": u.device, "events": u.event_count,
                       "last_seen_at": u.last_seen_at.isoformat()} for u in unmatched],
    }
