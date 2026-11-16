"""Engagement tracking: per-recipient tracked links, the raw click/open ledger, and attributed demo sessions.

Nothing here identifies a recipient from the outside. Tokens in URLs are random (``secrets.token_urlsafe``) and only
resolve to a recipient through these rows. Raw events are kept for troubleshooting and purged after the retention
period (Settings → Tracking); the deduplicated story lives in ``crm_activities``.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPk, utcnow

LINK_KINDS = ("link", "demo", "open_pixel")
EVENT_TYPES = ("click", "open")
DEMO_EVENT_TYPES = (
    "page_view",
    "application_started",
    "application_step",
    "signature_completed",
    "application_submitted",
    "form_submitted",
)


class TrackedLink(UUIDPk, Base):
    """One trackable link (or the open pixel) in one email to one recipient. Created when the email is rendered for
    sending; a retry or a reconcile of the same scheduled message reuses its rows, so the tokens in the email that
    actually went out always resolve."""

    __tablename__ = "tracked_links"
    __table_args__ = (
        UniqueConstraint("scheduled_message_id", "ordinal", name="uq_tracked_links_message_ordinal"),
        CheckConstraint(f"kind in {LINK_KINDS}", name="kind"),
    )

    token: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    scheduled_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheduled_messages.id", ondelete="SET NULL"), index=True)
    # filled in once the provider accepts the message
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sent_messages.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    sequence_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sequence_steps.id", ondelete="SET NULL"))
    ordinal: Mapped[int] = mapped_column(Integer)  # position in the email; 0 = open pixel
    destination_url: Mapped[str | None] = mapped_column(Text)  # exactly as written in the email, query string kept
    label: Mapped[str | None] = mapped_column(String(300))  # the words (or image alt) the recipient clicked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrackingEvent(UUIDPk, Base):
    """Raw ledger: every request to a tracked link or open pixel, including ones judged automated."""

    __tablename__ = "tracking_events"
    __table_args__ = (
        CheckConstraint(f"type in {EVENT_TYPES}", name="type"),
        Index("ix_tracking_events_contact_time", "contact_id", "occurred_at"),
        Index("ix_tracking_events_link_time", "link_id", "occurred_at"),
    )

    type: Mapped[str] = mapped_column(String(10))
    link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracked_links.id", ondelete="CASCADE"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # keyed hash, never the address itself
    ip_hash: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    method: Mapped[str] = mapped_column(String(8), default="GET")
    suspected_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    bot_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    # set when a later demo interaction from this click proves a person was there
    verified_human: Mapped[bool] = mapped_column(Boolean, default=False)

    link: Mapped[TrackedLink] = relationship()


class DemoSession(UUIDPk, Base):
    """A visit to the demo that arrived through a tracked campaign link. ``token`` is what the demo page holds (in its
    own first-party storage) and sends with each event; it is random and maps to the recipient only here."""

    __tablename__ = "demo_sessions"

    token: Mapped[str] = mapped_column(String(64), unique=True)
    link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracked_links.id", ondelete="CASCADE"), index=True)
    click_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tracking_events.id", ondelete="SET NULL"))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_count: Mapped[int] = mapped_column(Integer, default=0)


class DemoEvent(UUIDPk, Base):
    """A meaningful step on the demo. Never holds what the visitor typed: only which step, page and whether they left
    the sample values in place."""

    __tablename__ = "demo_events"
    __table_args__ = (
        CheckConstraint(f"type in {DEMO_EVENT_TYPES}", name="type"),
        Index("ix_demo_events_contact_time", "contact_id", "occurred_at"),
    )

    demo_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("demo_sessions.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(30))
    page: Mapped[str | None] = mapped_column(String(40))
    form: Mapped[str | None] = mapped_column(String(30))
    step: Mapped[int | None] = mapped_column(Integer)
    sample: Mapped[bool | None] = mapped_column(Boolean)  # visitor left the demo's example values untouched
    signed: Mapped[bool | None] = mapped_column(Boolean)
    user_agent: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SiteVisitor(UUIDPk, Base):
    """A browser on trace-studio.tech, as reported by the website's server (``POST /api/track/site-events``).

    ``vid`` is the website's own persistent visitor id. ``contact_id`` is set once the visitor is known: from ``rid``
    (the prospect id carried by the campaign link) or from an ``identify`` event whose email or phone matches a
    prospect. Name, email and phone are what the visitor typed into a website form about themselves."""

    __tablename__ = "site_visitors"

    vid: Mapped[str] = mapped_column(String(64), unique=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    rid: Mapped[str | None] = mapped_column(String(100))  # last recipient id seen, even when it matched nobody
    name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(60))
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(8))
    device: Mapped[str | None] = mapped_column(String(80))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=0)


class SiteEvent(UUIDPk, Base):
    """One step a visitor took on trace-studio.tech (demo video page, live demos). ``data`` holds only the keys the
    tracking spec lists for the event type; typed values, card/bank/PIN/password content and anything else are never
    stored. ``dedupe_key`` makes a retried batch a no-op."""

    __tablename__ = "site_events"
    __table_args__ = (
        Index("ix_site_events_contact_time", "contact_id", "occurred_at"),
        Index("ix_site_events_visitor_time", "visitor_id", "occurred_at"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    visitor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("site_visitors.id", ondelete="CASCADE"))
    sid: Mapped[str] = mapped_column(String(64), index=True)  # the website's session id (30 minutes of inactivity)
    rid: Mapped[str | None] = mapped_column(String(100))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(40))
    site: Mapped[str | None] = mapped_column(String(80))
    page: Mapped[str | None] = mapped_column(String(300))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    city: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(8))
    device: Mapped[str | None] = mapped_column(String(80))  # "iPhone · Safari", never the full user agent
    user_agent: Mapped[str | None] = mapped_column(String(300))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
