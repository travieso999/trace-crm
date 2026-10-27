"""Mailboxes, campaigns, sequences, queue, messages, conversations, events, suppression."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDelete, Timestamps, UUIDPk, utcnow
from app.models.crm import Contact

PROVIDERS = ("gmail", "microsoft", "sandbox")
MAILBOX_STATUSES = ("connected", "paused", "auth_error", "error", "disconnected")
CAMPAIGN_STATUSES = ("draft", "scheduled", "active", "paused", "completed", "stopped", "error")
CC_STATUSES = ("active", "replied", "completed", "unsubscribed", "bounced", "stopped", "removed", "error")
SM_STATUSES = ("queued", "sending", "sent", "failed", "cancelled", "unknown")
EVENT_TYPES = (
    "attempted",
    "accepted",
    "failed",
    "bounced",
    "replied",
    "auto_reply",
    "positive_reply",
    "negative_reply",
    "unsubscribed",
    "complaint",
    "meeting_booked",
)

DEFAULT_SCHEDULE = {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "timezone": "America/New_York"}


class EmailAccount(UUIDPk, Timestamps, Base):
    __tablename__ = "email_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "email", name="uq_email_accounts_provider_email"),
        CheckConstraint(f"provider in {PROVIDERS}", name="provider"),
        CheckConstraint(f"status in {MAILBOX_STATUSES}", name="status"),
    )

    provider: Mapped[str] = mapped_column(String(20))
    email: Mapped[str] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="connected")
    # Fernet-encrypted; never serialised to API responses
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)
    daily_limit: Mapped[int] = mapped_column(Integer, default=30)
    min_interval_seconds: Mapped[int] = mapped_column(Integer, default=120)
    schedule: Mapped[dict] = mapped_column(JSONB, default=lambda: dict(DEFAULT_SCHEDULE))
    signature_html: Mapped[str | None] = mapped_column(Text)
    sync_cursor: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


campaign_mailboxes = Table(
    "campaign_mailboxes",
    Base.metadata,
    Column("campaign_id", UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True),
    Column("email_account_id", UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"), primary_key=True),
)


class Campaign(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "campaigns"
    __table_args__ = (CheckConstraint(f"status in {CAMPAIGN_STATUSES}", name="status"),)

    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=50)
    # {"days":[0..6], "start":"09:00", "end":"17:00", "timezone":"America/New_York", "use_recipient_timezone":true}
    schedule: Mapped[dict] = mapped_column(JSONB, default=lambda: {**DEFAULT_SCHEDULE, "use_recipient_timezone": True})
    missing_variable_policy: Mapped[str] = mapped_column(String(20), default="block")  # block | fallback
    variable_fallbacks: Mapped[dict] = mapped_column(JSONB, default=dict)
    require_approved_personalization: Mapped[bool] = mapped_column(Boolean, default=False)
    stop_on_auto_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    steps: Mapped[list["SequenceStep"]] = relationship(
        back_populates="campaign", order_by="SequenceStep.position", cascade="all, delete-orphan"
    )
    mailboxes: Mapped[list[EmailAccount]] = relationship(secondary=campaign_mailboxes, lazy="selectin")


class SequenceStep(UUIDPk, Timestamps, Base):
    __tablename__ = "sequence_steps"
    __table_args__ = (UniqueConstraint("campaign_id", "position", name="uq_sequence_steps_campaign_position"),)

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # 1-based
    delay_days: Mapped[int] = mapped_column(Integer, default=0)  # wait before this step (ignored for step 1)
    business_days: Mapped[bool] = mapped_column(Boolean, default=True)
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    reply_in_thread: Mapped[bool] = mapped_column(Boolean, default=True)

    campaign: Mapped[Campaign] = relationship(back_populates="steps")


class EmailTemplate(UUIDPk, Timestamps, Base):
    __tablename__ = "email_templates"
    name: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")


class CampaignContact(UUIDPk, Timestamps, Base):
    __tablename__ = "campaign_contacts"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id", name="uq_campaign_contacts_campaign_contact"),
        CheckConstraint(f"status in {CC_STATUSES}", name="status"),
        Index("ix_campaign_contacts_due", "status", "next_send_at"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # position of last sent step
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="SET NULL")
    )
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    first_message_id: Mapped[str | None] = mapped_column(String(255))
    last_message_id: Mapped[str | None] = mapped_column(String(255))
    first_subject: Mapped[str | None] = mapped_column(String(500))
    stop_reason: Mapped[str | None] = mapped_column(String(100))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # lazy="select" (not joined) so SELECT ... FOR UPDATE on campaign_contacts never hits an outer join
    contact: Mapped[Contact] = relationship()
    campaign: Mapped[Campaign] = relationship()


class ScheduledMessage(UUIDPk, Timestamps, Base):
    __tablename__ = "scheduled_messages"
    __table_args__ = (
        UniqueConstraint("campaign_contact_id", "sequence_step_id", name="uq_scheduled_messages_cc_step"),
        CheckConstraint(f"status in {SM_STATUSES}", name="status"),
        Index("ix_scheduled_messages_status_due", "status", "due_at"),
    )

    campaign_contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_contacts.id", ondelete="CASCADE")
    )
    sequence_step_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("sequence_steps.id", ondelete="CASCADE"))
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    email_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    campaign_contact: Mapped[CampaignContact] = relationship()
    step: Mapped[SequenceStep] = relationship()


class Conversation(UUIDPk, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("email_account_id", "provider_thread_id", name="uq_conversations_account_thread"),)

    email_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"))
    provider_thread_id: Mapped[str] = mapped_column(String(255))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    subject: Mapped[str | None] = mapped_column(String(500))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    has_inbound: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=True)
    intent: Mapped[str | None] = mapped_column(String(40))

    contact: Mapped[Contact | None] = relationship()
    email_account: Mapped[EmailAccount] = relationship()


class SentMessage(UUIDPk, Base):
    __tablename__ = "sent_messages"
    __table_args__ = (Index("ix_sent_messages_account_sent", "email_account_id", "sent_at"),)

    scheduled_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scheduled_messages.id", ondelete="SET NULL"), unique=True
    )
    email_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    sent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(20), default="sequence")  # sequence | manual_reply | test
    to_email: Mapped[str] = mapped_column(String(320))
    from_email: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(500))
    body_html: Mapped[str] = mapped_column(Text)
    rfc_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReceivedMessage(UUIDPk, Base):
    __tablename__ = "received_messages"
    __table_args__ = (
        UniqueConstraint("email_account_id", "provider_message_id", name="uq_received_messages_account_msg"),
    )

    email_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    provider_message_id: Mapped[str] = mapped_column(String(255))
    provider_thread_id: Mapped[str | None] = mapped_column(String(255))
    rfc_message_id: Mapped[str | None] = mapped_column(String(255), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(255))
    from_email: Mapped[str] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(200))
    to_email: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(String(500))
    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)  # sanitised
    kind: Mapped[str] = mapped_column(String(20), default="reply")  # reply|auto_reply|bounce|unsubscribe|unmatched
    intent: Mapped[str | None] = mapped_column(String(40))
    intent_confidence: Mapped[float | None] = mapped_column()
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EmailEvent(UUIDPk, Base):
    __tablename__ = "email_events"
    __table_args__ = (
        Index("ix_email_events_type_time", "type", "occurred_at"),
        CheckConstraint(f"type in {EVENT_TYPES}", name="type"),
    )

    type: Mapped[str] = mapped_column(String(30))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), index=True)
    email_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("email_accounts.id", ondelete="SET NULL"), index=True
    )
    sent_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sent_messages.id", ondelete="SET NULL"))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SuppressionEntry(UUIDPk, Base):
    __tablename__ = "suppression_entries"

    email: Mapped[str] = mapped_column(String(320), unique=True)
    reason: Mapped[str] = mapped_column(String(30))  # unsubscribe|bounce|complaint|manual|reply_request
    source: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
