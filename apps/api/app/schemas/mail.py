import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel
from app.services.audience import AudienceFilter


class ScheduleIn(BaseModel):
    days: list[int] = Field(default=[0, 1, 2, 3, 4])
    start: str = "09:00"
    end: str = "17:00"
    timezone: str = "America/New_York"
    use_recipient_timezone: bool | None = None

    @field_validator("days")
    @classmethod
    def _days(cls, v):
        if not v or any(d not in range(7) for d in v):
            raise ValueError("days must be a non-empty list of weekday numbers 0 (Mon) – 6 (Sun)")
        return sorted(set(v))

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError("Times must be HH:MM (24h)")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v):
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(v)
        except Exception as exc:
            raise ValueError("Unknown IANA time zone") from exc
        return v


class EmailAccountOut(ORMModel):
    id: uuid.UUID
    provider: str
    email: str
    display_name: str | None
    status: str
    daily_limit: int
    min_interval_seconds: int
    schedule: dict
    signature_html: str | None
    last_synced_at: datetime | None
    last_sent_at: datetime | None
    consecutive_failures: int
    circuit_open_until: datetime | None
    last_error: str | None
    last_error_at: datetime | None
    token_expires_at: datetime | None
    scopes: str | None
    server: dict | None = None  # smtp mailboxes: hosts and ports (the password is never returned)
    created_at: datetime
    sent_today: int = 0
    sent_total: int = 0
    campaigns: list[dict] = []


class EmailAccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    daily_limit: int | None = Field(default=None, ge=1, le=500)
    min_interval_seconds: int | None = Field(default=None, ge=30, le=3600)
    schedule: ScheduleIn | None = None
    signature_html: str | None = Field(default=None, max_length=5000)


class StepIn(BaseModel):
    id: uuid.UUID | None = None
    delay_days: int = Field(default=0, ge=0, le=60)
    business_days: bool = True
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20000)
    reply_in_thread: bool = True
    name: str | None = Field(default=None, max_length=120)
    status: str = Field(default="active", pattern="^(draft|active|paused)$")
    send_at: datetime | None = None  # not sent before this moment (in addition to the delay)
    video_asset_id: uuid.UUID | None = None
    video_preview: bool = False  # include the clickable preview
    video_attach: bool = False  # attach the actual MP4 file


class StepOut(ORMModel):
    id: uuid.UUID
    position: int
    delay_days: int
    business_days: bool
    subject: str
    body: str
    reply_in_thread: bool
    name: str | None = None
    status: str = "active"
    send_at: datetime | None = None
    video_asset_id: uuid.UUID | None = None
    video_preview: bool = False
    video_attach: bool = False


class CampaignIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    daily_limit: int | None = Field(default=None, ge=1, le=1000)
    schedule: ScheduleIn | None = None
    missing_variable_policy: str | None = Field(default=None, pattern="^(block|fallback)$")
    variable_fallbacks: dict[str, str] | None = None
    require_approved_personalization: bool | None = None
    stop_on_auto_reply: bool | None = None
    start_at: datetime | None = None
    mailbox_ids: list[uuid.UUID] | None = None
    campaign_type: str | None = Field(default=None, max_length=60)
    target_location: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=120)
    audience_mode: str | None = Field(default=None, pattern="^(fixed|dynamic)$")
    audience_filter: AudienceFilter | None = None


class CampaignCreate(CampaignIn):
    name: str = Field(min_length=1, max_length=200)


class CampaignOut(ORMModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    daily_limit: int
    schedule: dict
    missing_variable_policy: str
    variable_fallbacks: dict
    require_approved_personalization: bool
    stop_on_auto_reply: bool
    start_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    archived_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    paused_at: datetime | None = None
    campaign_type: str | None = None
    target_location: str | None = None
    industry: str | None = None
    audience_mode: str = "fixed"
    audience_filter: dict = {}
    import_lists: list[dict] = []  # imported lists this campaign drew prospects from
    steps: list[StepOut] = []
    mailbox_ids: list[uuid.UUID] = []
    stats: dict = {}


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20000)


class TemplateOut(ORMModel):
    id: uuid.UUID
    name: str
    subject: str
    body: str
    created_at: datetime


class PreviewIn(BaseModel):
    subject: str = Field(max_length=500)
    body: str = Field(max_length=20000)
    contact_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    video_asset_id: uuid.UUID | None = None
    video_preview: bool = False
    video_attach: bool = False


class TestSendIn(PreviewIn):
    to_email: str
    email_account_id: uuid.UUID


class ReplyIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class SuppressionIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=5000)
    reason: str = Field(default="manual", pattern="^(unsubscribe|bounce|complaint|manual|reply_request)$")


class SuppressionOut(ORMModel):
    id: uuid.UUID
    email: str
    reason: str
    source: str | None
    created_at: datetime
