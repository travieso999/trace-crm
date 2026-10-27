import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.schemas.common import ORMModel


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    mfa_enabled: bool = False


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    # true for an admin who must turn on two-step verification before admin features work
    mfa_setup_required: bool = False


class MfaChallengeOut(BaseModel):
    mfa_required: bool = True
    mfa_token: str


class MfaLoginIn(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=400)
    code: str = Field(min_length=6, max_length=40)


class MfaSetupIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class MfaEnableIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class MfaDisableIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=40)


class AuditEntryOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    action: str
    user_email: str | None
    entity_type: str | None
    entity_id: str | None
    ip_address: str | None
    data: dict


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern="^(admin|member|viewer)$")
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
