import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, computed_field

from app.core import permissions
from app.schemas.common import ORMModel

ROLE_PATTERN = "^(admin|member|viewer)$"
# no "@", so a sign-in name is never mistaken for an email address
USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,59}$"


class LoginIn(BaseModel):
    # email address or username (the field keeps its name so existing clients keep working)
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    username: str | None = None
    full_name: str
    role: str
    is_active: bool
    last_login_at: datetime | None
    permission_overrides: dict = {}
    created_at: datetime | None = None
    password_changed_at: datetime | None = None
    mfa_enabled: bool = False
    mfa_method: str = "totp"

    @computed_field
    @property
    def permissions(self) -> list[str]:
        return sorted(permissions.effective(self.role, self.permission_overrides))


class MeOut(BaseModel):
    user: UserOut
    csrf_token: str
    # true for an admin who must turn on two-step verification before admin features work
    mfa_setup_required: bool = False


class MfaChallengeOut(BaseModel):
    mfa_required: bool = True
    mfa_token: str
    method: str = "totp"  # totp | email
    sent_to: str | None = None  # masked address the code went to (email method)
    email_sent: bool | None = None  # False when the code email could not be sent (a recovery code still works)


class MfaResendIn(BaseModel):
    mfa_token: str = Field(min_length=1, max_length=400)


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


class TeamMemberOut(ORMModel):
    """What every signed-in user may see about teammates: enough to show and pick an owner."""

    id: uuid.UUID
    full_name: str
    username: str | None = None
    email: str
    role: str
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    username: str | None = Field(default=None, pattern=USERNAME_PATTERN)
    full_name: str = Field(default="", max_length=200)
    password: str = Field(min_length=12, max_length=256)
    role: str = Field(default="member", pattern=ROLE_PATTERN)
    permission_overrides: dict[str, bool] = Field(default_factory=dict)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    username: str | None = Field(default=None, pattern=USERNAME_PATTERN)
    full_name: str | None = Field(default=None, max_length=200)
    role: str | None = Field(default=None, pattern=ROLE_PATTERN)
    is_active: bool | None = None
    permission_overrides: dict[str, bool] | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=12, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)
