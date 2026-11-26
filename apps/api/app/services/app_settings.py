"""Application settings stored as JSONB rows, merged over defaults."""

import copy

from sqlalchemy.orm import Session

from app.models import AppSetting

DEFAULTS: dict[str, dict] = {
    "company": {"name": "Trace Studio", "website": "", "phone": ""},
    "sending_identity": {"default_from_name": "", "reply_to": ""},
    "compliance": {
        "physical_address": "",
        "footer_template": (
            "{{company_name_sender}} · {{physical_address}}\n"
            "Not interested? Unsubscribe here: {{unsubscribe_url}}"
        ),
        "list_unsubscribe_header": True,
        "suppress_on_unsubscribe_reply": True,
    },
    "defaults": {
        "daily_limit_per_mailbox": 30,
        "campaign_daily_limit": 50,
        "min_interval_seconds": 120,
        "schedule": {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "timezone": "America/New_York"},
        "timezone": "America/New_York",
    },
    "ai": {"enabled": True, "model": "", "daily_token_budget": 200_000, "max_retries": 2, "classify_replies": True},
    "notifications": {"notify_on_reply": True, "notification_email": "", "notify_on_mailbox_error": True,
                      "email_task_reminders": True,
                      # end-of-day summary (services/daily_summary.py): to daily_summary_email, else notification_email,
                      # on the default schedule's sending days, at the first engine pass after this hour (defaults
                      # timezone)
                      "daily_summary": True, "daily_summary_email": "", "daily_summary_hour": 17},
    "crm": {"task_types": ["Call", "Email", "Follow-up", "Meeting", "Proposal", "Payment Reminder", "Other"]},
    "security": {"session_ttl_hours": 12},
    "retention": {"received_body_days": 365, "audit_log_days": 730, "sandbox_outbox_days": 14},
    "backup": {"schedule": "daily 03:00 UTC", "retention_days": 14, "location": "./backups"},
    # never above what the sending provider supports: see video_assets.max_attachment_bytes
    "video_attachments": {"max_attachment_mb": 17, "warn_attachment_mb": 10, "library_max_mb": 200},
    # Engagement tracking (services/tracking.py). Open tracking adds a 1x1 image: off by default because pixels can
    # cost deliverability and opens are unreliable (Apple Mail Privacy Protection, image proxies).
    "tracking": {
        "click_tracking": True,
        "open_tracking": False,
        # links whose address starts with one of these count as the demo ("Watch 360° Demo" and the video block)
        # (each gets ?rid=<prospect id> added in campaign emails, so the website can tell who it is)
        "demo_urls": ["https://trace-studio.tech/demos/ellison-reyes", "https://trace-studio.tech/demo"],
        # sites allowed to report demo steps to /api/track/demo
        "demo_origins": ["https://trace-studio.tech"],
        # a click this soon after the email was accepted is almost always a security scanner
        "scanner_window_seconds": 20,
        # raw click/open/demo events are deleted after this many days (timeline entries stay)
        "retention_days": 365,
    },
}


class SettingsError(ValueError):
    pass


def get_section(db: Session, key: str) -> dict:
    if key not in DEFAULTS:
        raise SettingsError(f"Unknown settings section '{key}'")
    row = db.get(AppSetting, key)
    merged = copy.deepcopy(DEFAULTS[key])
    if row and row.value:
        merged.update(row.value)
    return merged


def get_all(db: Session) -> dict:
    return {k: get_section(db, k) for k in DEFAULTS}


def validate_section(key: str, value: dict) -> dict:
    allowed = set(DEFAULTS[key])
    unknown = set(value) - allowed
    if unknown:
        raise SettingsError(f"Unknown fields for {key}: {', '.join(sorted(unknown))}")
    if key == "compliance" and "footer_template" in value:
        if "{{unsubscribe_url}}" not in value["footer_template"]:
            raise SettingsError("The footer must contain {{unsubscribe_url}}")
        if "{{physical_address}}" not in value["footer_template"]:
            raise SettingsError("The footer must contain {{physical_address}}")
    if key == "defaults" and "schedule" in value:
        from app.schemas.mail import ScheduleIn

        value["schedule"] = ScheduleIn.model_validate(value["schedule"]).model_dump()
    if key == "crm" and "task_types" in value:
        types = [str(t).strip() for t in value["task_types"] or [] if str(t).strip()]
        if not types or len(types) > 30 or any(len(t) > 40 for t in types):
            raise SettingsError("Task types: 1-30 names of up to 40 characters")
        value["task_types"] = list(dict.fromkeys(types))
    if key == "notifications":
        from app.services.normalize import InvalidEmail, normalize_email

        for name in ("notification_email", "daily_summary_email"):
            if value.get(name):
                try:
                    value[name] = normalize_email(value[name])
                except InvalidEmail as exc:
                    raise SettingsError(f"{name}: {exc}") from exc
        if "daily_summary" in value and not isinstance(value["daily_summary"], bool):
            raise SettingsError("daily_summary must be true or false")
        hour = value.get("daily_summary_hour", 17)
        if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
            raise SettingsError("daily_summary_hour must be a whole hour from 0 to 23")
    if key == "ai":
        if int(value.get("daily_token_budget", 1)) < 0 or int(value.get("max_retries", 0)) not in range(0, 6):
            raise SettingsError("Invalid AI limits")
    if key == "tracking":
        for flag in ("click_tracking", "open_tracking"):
            if flag in value and not isinstance(value[flag], bool):
                raise SettingsError(f"{flag} must be true or false")
        for name in ("demo_urls", "demo_origins"):
            if name in value:
                items = [str(u).strip() for u in value[name] or [] if str(u).strip()]
                if len(items) > 20 or any(len(u) > 500 or not u.startswith("https://") for u in items):
                    raise SettingsError(f"{name}: up to 20 https:// addresses")
                value[name] = list(dict.fromkeys(items))
        for name, low, high in (("scanner_window_seconds", 0, 600), ("retention_days", 30, 3650)):
            if name in value and (isinstance(value[name], bool) or not isinstance(value[name], int)
                                  or not low <= value[name] <= high):
                raise SettingsError(f"{name} must be a whole number from {low} to {high}")
    if key == "video_attachments":
        for name, number in value.items():
            if isinstance(number, bool) or not isinstance(number, int | float) or not 0 < number <= 100_000:
                raise SettingsError(f"{name} must be a positive number of megabytes")
    return value


def update_section(db: Session, key: str, value: dict) -> dict:
    value = validate_section(key, value)
    row = db.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key, value={})
        db.add(row)
    row.value = {**(row.value or {}), **value}
    db.flush()
    return get_section(db, key)


def compliance_ready(db: Session) -> list[str]:
    c = get_section(db, "compliance")
    company = get_section(db, "company")
    problems = []
    if len((c.get("physical_address") or "").strip()) < 10:
        problems.append("A valid physical postal address must be configured in Settings → Compliance")
    if not (company.get("name") or "").strip():
        problems.append("Company name must be configured in Settings → Company")
    return problems
