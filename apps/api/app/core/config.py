from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../../.env"), extra="ignore")

    app_env: str = "development"  # development | test | production
    app_name: str = "Trace CRM"
    log_level: str = "INFO"

    # Public URL the browser uses (used for OAuth redirects and unsubscribe links)
    public_url: str = "http://localhost:3000"

    database_url: str = "postgresql+psycopg://outreach:outreach@localhost:5432/outreach"
    redis_url: str | None = None
    celery_broker_url: str | None = None

    # Comma-separated Fernet keys; first key encrypts, all keys decrypt (rotation)
    token_encryption_keys: str = ""
    # HMAC secret for unsubscribe tokens
    secret_key: str = ""

    session_ttl_hours: int = 12
    # A session unused for this long ends even before its TTL (an unattended, unlocked laptop)
    session_idle_minutes: int = 120
    session_cookie_name: str = "ts_session"
    csrf_cookie_name: str = "ts_csrf"
    cookie_secure: bool = False
    # Only trust X-Forwarded-For when running behind our own reverse proxy
    trust_proxy_headers: bool = False

    login_rate_limit_per_minute: int = 10
    # Lockout: this many failed sign-ins for one email (or 3x from one IP) within the window locks it for the window
    login_lockout_failures: int = 8
    login_lockout_minutes: int = 15
    api_rate_limit_per_minute: int = 600
    # Bulk prospect exports per admin per hour
    export_limit_per_hour: int = 10

    # Admins must turn on two-step verification before admin features work. Defaults to on in production.
    require_admin_mfa: bool | None = None

    # OAuth providers (optional)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # Defaults to {PUBLIC_URL}/api/email-accounts/oauth/google/callback; must match Google Cloud Console exactly
    google_redirect_uri: str | None = None

    # Real (non-sandbox) sending: "disabled" (default) | "test_only" (only allow-listed test emails) | "enabled".
    # Reply sync from connected mailboxes works in every mode.
    real_sending: str = "disabled"
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    microsoft_tenant: str = "common"

    # AI provider (optional)
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"

    # Email verification provider (optional): "local" (syntax + MX) or "neverbounce"
    verification_provider: str = "local"
    neverbounce_api_key: str | None = None

    # Sandbox mailbox provider (writes .eml files instead of sending). Never enabled in production.
    sandbox_outbox_dir: str = "./var/sandbox_outbox"

    csv_max_bytes: int = 10 * 1024 * 1024
    csv_max_rows: int = 25_000

    allowed_test_recipients: str = ""  # comma separated; test sends restricted to these + users

    # Task-reminder emails (optional). A separate SMTP channel, never the campaign mailboxes: it only ever writes to
    # the notification address in Settings → Notifications. Off unless host and from address are set.
    notify_smtp_host: str | None = None
    notify_smtp_port: int = 587
    notify_smtp_username: str | None = None
    notify_smtp_password: str | None = None
    notify_smtp_from: str | None = None
    notify_smtp_starttls: bool = True

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        if v not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production")
        return v

    @field_validator("real_sending")
    @classmethod
    def _real_sending(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"disabled", "test_only", "enabled"}:
            raise ValueError("REAL_SENDING must be disabled, test_only or enabled")
        return v

    @property
    def google_oauth_redirect_uri(self) -> str:
        return self.google_redirect_uri or f"{self.public_url.rstrip('/')}/api/email-accounts/oauth/google/callback"

    @property
    def google_oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.token_encryption_keys)

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def admin_mfa_required(self) -> bool:
        return self.is_production if self.require_admin_mfa is None else self.require_admin_mfa

    @property
    def sandbox_enabled(self) -> bool:
        return not self.is_production

    @property
    def broker_url(self) -> str:
        if self.celery_broker_url:
            return self.celery_broker_url
        if self.redis_url:
            return self.redis_url
        # Dev fallback: Postgres-backed broker (kombu SQLAlchemy transport) when Redis is absent
        return "sqla+" + self.database_url

    @property
    def notify_email_configured(self) -> bool:
        return bool(self.notify_smtp_host and self.notify_smtp_from)

    @property
    def test_recipient_allowlist(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_test_recipients.split(",") if e.strip()}

    def validate_for_runtime(self) -> list[str]:
        problems: list[str] = []
        if not self.token_encryption_keys:
            problems.append("TOKEN_ENCRYPTION_KEYS is not set")
        if len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters")
        if self.is_production:
            if not self.cookie_secure:
                problems.append("COOKIE_SECURE must be true in production")
            if not self.public_url.startswith("https://"):
                problems.append("PUBLIC_URL must be an https:// address in production")
            db = urlsplit(self.database_url)
            if not db.password or db.password == "outreach" or db.username == "postgres":  # noqa: S105 - known default
                problems.append("DATABASE_URL must use a dedicated database user with a strong password in production")
            if not self.admin_mfa_required:
                problems.append("REQUIRE_ADMIN_MFA must not be disabled in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_field = Field  # re-export for convenience
