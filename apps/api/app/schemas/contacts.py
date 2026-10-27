import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.crm import LEAD_STATUSES, VERIFICATION_STATUSES
from app.schemas.common import ORMModel

_LEAD = "^(" + "|".join(LEAD_STATUSES) + ")$"
_VERIFY = "^(" + "|".join(VERIFICATION_STATUSES) + ")$"


class ContactBase(BaseModel):
    first_name: str | None = Field(default=None, max_length=120)
    last_name: str | None = Field(default=None, max_length=120)
    company_name: str | None = Field(default=None, max_length=300)
    company_id: uuid.UUID | None = None
    website: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=60)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=60)
    timezone: str | None = Field(default=None, max_length=64)
    industry: str | None = Field(default=None, max_length=120)
    job_title: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=20000)
    source: str | None = Field(default=None, max_length=120)
    verification_status: str | None = Field(default=None, pattern=_VERIFY)
    lead_status: str | None = Field(default=None, pattern=_LEAD)
    custom_fields: dict[str, str] | None = None
    tags: list[str] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        if v:
            from zoneinfo import ZoneInfo

            try:
                ZoneInfo(v)
            except Exception as exc:
                raise ValueError("Unknown IANA time zone") from exc
        return v

    @field_validator("custom_fields")
    @classmethod
    def _custom(cls, v):
        if v is None:
            return v
        import re

        for k in v:
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", k):
                raise ValueError(f"Invalid custom field name '{k}' (use lowercase letters, digits, underscore)")
        return v


class ContactCreate(ContactBase):
    email: EmailStr


class ContactUpdate(ContactBase):
    email: EmailStr | None = None


class TagOut(ORMModel):
    id: uuid.UUID
    name: str
    color: str | None


class ContactOut(ORMModel):
    id: uuid.UUID
    email: str
    first_name: str | None
    last_name: str | None
    company_id: uuid.UUID | None
    company_name: str | None
    website: str | None
    phone: str | None
    city: str | None
    state: str | None
    timezone: str | None
    industry: str | None
    job_title: str | None
    linkedin_url: str | None
    notes: str | None
    source: str | None
    verification_status: str
    lead_status: str
    custom_fields: dict
    tags: list[TagOut]
    last_contacted_at: datetime | None
    last_replied_at: datetime | None
    created_at: datetime
    updated_at: datetime
    suppressed: bool = False
    campaigns: list[dict] = []


class ActivityOut(ORMModel):
    id: uuid.UUID
    type: str
    summary: str
    data: dict
    created_at: datetime
    user_id: uuid.UUID | None


class BulkContactAction(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=5000)
    action: str = Field(pattern="^(delete|set_status|add_tag|remove_tag|assign_campaign|set_verification)$")
    value: str | None = None


class CsvPreviewOut(BaseModel):
    headers: list[str]
    sample_rows: list[list[str]]
    total_rows: int
    suggested_mapping: dict[str, str]
    fields: list[str]


class CsvImportIn(BaseModel):
    mapping: dict[str, str]  # csv header -> field name ("" to skip, "custom:<name>" for custom field)
    duplicate_strategy: str = Field(default="skip", pattern="^(skip|update)$")
    tags: list[str] = []
    source: str | None = None
    campaign_id: uuid.UUID | None = None
    dry_run: bool = False


class CsvRowError(BaseModel):
    row: int
    email: str | None
    error: str


class CsvImportOut(BaseModel):
    created: int
    updated: int
    skipped_duplicates: int
    skipped_suppressed: int
    invalid: int
    errors: list[CsvRowError]
    dry_run: bool


class CompanyBase(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=60)
    timezone: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=20000)


class CompanyCreate(CompanyBase):
    name: str = Field(min_length=1, max_length=300)


class CompanyOut(ORMModel):
    id: uuid.UUID
    name: str
    website: str | None
    domain: str | None
    industry: str | None
    city: str | None
    state: str | None
    timezone: str | None
    website_status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime
    contact_count: int = 0
    latest_audit: dict | None = None
