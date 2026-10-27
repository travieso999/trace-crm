from functools import lru_cache

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
    session_cookie_name: str = "ts_session"
    csrf_cookie_name: str = "ts_csrf"
    cookie_secure: bool = False
    # Only trust X-Forwarded-For when running behind our own reverse proxy
    trust_proxy_headers: bool = False

    login_rate_limit_per_minute: int = 10
    api_rate_limit_per_minute: int = 600

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

    # Bearer secret for GET /api/cron/engine (Vercel Cron and the external per-minute pinger). Endpoint is off when unset.
    cron_secret: str | None = None

    @field_validator("database_url")
    @classmethod
    def _database_url(cls, v: str) -> str:
        # Hosted Postgres (Neon, Vercel) hands out postgres:// URLs; SQLAlchemy needs the psycopg driver named
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

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
        if self.is_production and not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        if self.cron_secret is not None and len(self.cron_secret) < 32:
            problems.append("CRON_SECRET must be at least 32 characters")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings_field = Field  # re-export for convenience
