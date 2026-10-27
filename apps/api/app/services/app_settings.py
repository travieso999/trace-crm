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
    "notifications": {"notify_on_reply": True, "notification_email": "", "notify_on_mailbox_error": True},
    "security": {"session_ttl_hours": 12},
    "retention": {"received_body_days": 365, "audit_log_days": 730, "sandbox_outbox_days": 14},
    "backup": {"schedule": "daily 03:00 UTC", "retention_days": 14, "location": "./backups"},
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
    if key == "ai":
        if int(value.get("daily_token_budget", 1)) < 0 or int(value.get("max_retries", 0)) not in range(0, 6):
            raise SettingsError("Invalid AI limits")
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
