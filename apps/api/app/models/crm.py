"""Companies, contacts, tags, CRM activities/opportunities/tasks, audits, AI personalization."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
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


class CrmTask(UUIDPk, Timestamps, Base):
    __tablename__ = "crm_tasks"
    __table_args__ = (Index("ix_crm_tasks_open", "completed_at", "due_at"),)

    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


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
