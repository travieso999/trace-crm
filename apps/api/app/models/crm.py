"""Companies, contacts, tags, CRM activities/opportunities/tasks, audits, AI personalization."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, SoftDelete, Timestamps, UUIDPk, utcnow

LEAD_STATUSES = (
    "new_lead",
    "contacted",
    "replied",
    "interested",
    "meeting_scheduled",
    "proposal_sent",
    "won",
    "lost",
    "not_interested",
)
VERIFICATION_STATUSES = ("unknown", "valid", "risky", "invalid")


class Company(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "companies"
    __table_args__ = (Index("ix_companies_name_lower", func.lower(text("name"))),)

    name: Mapped[str] = mapped_column(String(300))
    website: Mapped[str | None] = mapped_column(String(500))
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    industry: Mapped[str | None] = mapped_column(String(120))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(60), index=True)
    timezone: Mapped[str | None] = mapped_column(String(64))
    website_status: Mapped[str] = mapped_column(String(30), default="unknown")  # unknown|online|offline|error
    notes: Mapped[str | None] = mapped_column(Text)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="company")


contact_tags = Table(
    "contact_tags",
    Base.metadata,
    Column("contact_id", UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(UUIDPk, Base):
    __tablename__ = "tags"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    color: Mapped[str | None] = mapped_column(String(20))


class Contact(UUIDPk, Timestamps, SoftDelete, Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("uq_contacts_email_active", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
        CheckConstraint(f"lead_status in {LEAD_STATUSES}", name="lead_status"),
        CheckConstraint(f"verification_status in {VERIFICATION_STATUSES}", name="verification_status"),
    )

    email: Mapped[str] = mapped_column(String(320))
    first_name: Mapped[str | None] = mapped_column(String(120))
    last_name: Mapped[str | None] = mapped_column(String(120))
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    company_name: Mapped[str | None] = mapped_column(String(300))
    website: Mapped[str | None] = mapped_column(String(500))
    phone: Mapped[str | None] = mapped_column(String(60))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(60), index=True)
    timezone: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(120))
    job_title: Mapped[str | None] = mapped_column(String(200))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(120))
    verification_status: Mapped[str] = mapped_column(String(20), default="unknown")
    lead_status: Mapped[str] = mapped_column(String(30), default="new_lead", index=True)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company | None] = relationship(back_populates="contacts")
    tags: Mapped[list[Tag]] = relationship(secondary=contact_tags, lazy="selectin")


class CrmActivity(UUIDPk, Base):
    __tablename__ = "crm_activities"
    __table_args__ = (Index("ix_crm_activities_contact_created", "contact_id", "created_at"),)

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    type: Mapped[str] = mapped_column(String(40))  # note|stage_change|email_sent|reply|import|campaign|unsubscribe|bounce|...
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PipelineStage(UUIDPk, Timestamps, Base):
    """An Opportunities board column. ``lead_status`` links it to the lead lifecycle the automation uses: moving a
    card into a linked stage sets that status, and automation moves cards that sit in linked stages. Stages the
    user adds are unlinked unless they choose a status."""

    __tablename__ = "pipeline_stages"

    name: Mapped[str] = mapped_column(String(80))
    color: Mapped[str] = mapped_column(String(20), default="#8a8579")
    position: Mapped[int] = mapped_column(Integer, default=0)
    lead_status: Mapped[str | None] = mapped_column(String(30), unique=True)


class CrmOpportunity(UUIDPk, Timestamps, Base):
    __tablename__ = "crm_opportunities"

    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), unique=True
    )
    value_cents: Mapped[int | None] = mapped_column(BigInteger)
    meeting_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # explicit board column; NULL = the stage linked to the lead's status
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_stages.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="open", server_default="open")  # open|won|lost
    # on the Opportunities board: only opportunities created there (prospects imported for campaigns are not)
    on_board: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"), index=True)


class CrmTask(UUIDPk, Timestamps, Base):
    __tablename__ = "crm_tasks"
    __table_args__ = (
        Index("ix_crm_tasks_open", "completed_at", "due_at"),
        Index("ix_crm_tasks_reminder_due", "reminder_at", postgresql_where=text("reminder_sent_at IS NULL")),
    )

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(40), default="Follow-up", server_default="Follow-up")
    timezone: Mapped[str | None] = mapped_column(String(64))  # IANA zone the due date/time was entered in
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    # reminder: minutes before due_at (0 = at due time); reminder_at is derived and reset when due/offset change
    reminder_minutes: Mapped[int | None] = mapped_column(Integer)
    reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overdue_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrmNote(UUIDPk, Timestamps, Base):
    """A note on an opportunity. Edits keep the previous text as a CrmNoteRevision; archiving hides, never deletes."""

    __tablename__ = "crm_notes"
    __table_args__ = (Index("ix_crm_notes_contact_created", "contact_id", "created_at"),)

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class CrmNoteRevision(UUIDPk, Base):
    """The text a note had before an edit (``replaced_at`` = when that version was replaced)."""

    __tablename__ = "crm_note_revisions"

    note_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_notes.id", ondelete="CASCADE"), index=True)
    body: Mapped[str] = mapped_column(Text)
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    replaced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(UUIDPk, Base):
    """In-app notification for one user. (user, task, kind, fire_at) is unique, so a reminder is created once."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("user_id", "task_id", "kind", "fire_at", name="uq_notifications_once"),
        Index("ix_notifications_user_unread", "user_id", "read_at", "created_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30))  # task_reminder | task_overdue
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_tasks.id", ondelete="SET NULL"))
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # optional email copy to the configured notification address: none | pending | sent | failed
    email_status: Mapped[str] = mapped_column(String(20), default="none", server_default="none")
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_error: Mapped[str | None] = mapped_column(Text)


class WebsiteAudit(UUIDPk, Timestamps, Base):
    __tablename__ = "website_audits"

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(500))
    final_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|completed|failed|blocked
    http_status: Mapped[int | None] = mapped_column(Integer)
    checks: Mapped[list] = mapped_column(JSONB, default=list)  # [{key,label,result,evidence}]
    summary: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiPersonalization(UUIDPk, Timestamps, Base):
    __tablename__ = "ai_personalizations"

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="opening_line")
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    generated_text: Mapped[str | None] = mapped_column(Text)
    final_text: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|insufficient_data|error
    model: Mapped[str | None] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
