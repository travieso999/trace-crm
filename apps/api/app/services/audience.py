"""Prospect filters, shared by the Prospects list and export, campaign enrollment ("smart filters") and dynamic
campaign audiences. One definition, so the count shown before adding is exactly who gets added."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, func, or_, select

from app.models import CampaignContact, Contact, Tag, contact_tags, import_batch_members


class AudienceFilter(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    lead_status: str | None = Field(default=None, max_length=300)  # comma separated
    verification_status: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, max_length=300)  # comma separated, e.g. "TX,OK"
    city: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    tag: str | None = Field(default=None, max_length=80)
    batch_id: uuid.UUID | None = None  # imported list
    campaign_id: uuid.UUID | None = None  # in this campaign
    not_in_campaign_id: uuid.UUID | None = None  # not (yet) in this campaign
    # "never": never added to any campaign; "previous": was in at least one campaign
    participation: Literal["never", "previous"] | None = None
    company_id: uuid.UUID | None = None
    custom: dict[str, str] = Field(default_factory=dict)  # custom field name -> value (case-insensitive match)

    @field_validator("custom")
    @classmethod
    def _custom(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 10:
            raise ValueError("At most 10 custom field filters")
        return {k[:100]: str(val)[:200] for k, val in v.items() if k and str(val).strip()}

    def is_empty(self) -> bool:
        """True when nothing narrows the audience: adding "everyone" must be an explicit choice, never an accident."""
        data = self.model_dump(exclude={"not_in_campaign_id"}, exclude_none=True)
        return not any(v for v in data.values())

    def to_json(self) -> dict:
        return self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


def contacts_query(f: AudienceFilter) -> Select:
    """Active (not deleted) prospects matching every given condition."""
    stmt = select(Contact).where(Contact.deleted_at.is_(None))
    if f.q:
        like = f"%{f.q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(Contact.email).like(like),
            func.lower(Contact.first_name).like(like),
            func.lower(Contact.last_name).like(like),
            func.lower(Contact.company_name).like(like),
            func.lower(Contact.city).like(like),
        ))
    if f.lead_status:
        stmt = stmt.where(Contact.lead_status.in_(f.lead_status.split(",")))
    if f.verification_status:
        stmt = stmt.where(Contact.verification_status == f.verification_status)
    if f.state:
        stmt = stmt.where(func.upper(Contact.state).in_([s.strip().upper() for s in f.state.split(",") if s.strip()]))
    if f.city:
        stmt = stmt.where(func.lower(Contact.city) == f.city.strip().lower())
    if f.industry:
        stmt = stmt.where(func.lower(Contact.industry).like(f"%{f.industry.strip().lower()}%"))
    if f.tag:
        stmt = stmt.where(Contact.id.in_(select(contact_tags.c.contact_id).join(Tag).where(Tag.name == f.tag)))
    if f.batch_id:
        stmt = stmt.where(Contact.id.in_(
            select(import_batch_members.c.contact_id).where(import_batch_members.c.batch_id == f.batch_id)))
    if f.campaign_id:
        stmt = stmt.where(Contact.id.in_(
            select(CampaignContact.contact_id).where(CampaignContact.campaign_id == f.campaign_id)))
    if f.not_in_campaign_id:
        stmt = stmt.where(Contact.id.not_in(
            select(CampaignContact.contact_id).where(CampaignContact.campaign_id == f.not_in_campaign_id)))
    if f.participation == "never":
        stmt = stmt.where(Contact.id.not_in(select(CampaignContact.contact_id)))
    elif f.participation == "previous":
        stmt = stmt.where(Contact.id.in_(select(CampaignContact.contact_id)))
    if f.company_id:
        stmt = stmt.where(Contact.company_id == f.company_id)
    for key, value in f.custom.items():
        stmt = stmt.where(func.lower(Contact.custom_fields[key].astext) == value.strip().lower())
    return stmt


def count(db, f: AudienceFilter) -> int:
    return db.scalar(select(func.count()).select_from(contacts_query(f).subquery())) or 0
