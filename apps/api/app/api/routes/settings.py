from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, reader
from app.core.config import get_settings
from app.core.db import get_db
from app.models import User
from app.services import app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_all(_: User = Depends(reader), db: Session = Depends(get_db)):
    s = get_settings()
    return {
        "sections": app_settings.get_all(db),
        "integrations": {
            "google_oauth_configured": bool(s.google_client_id and s.google_client_secret),
            "microsoft_oauth_configured": bool(s.microsoft_client_id and s.microsoft_client_secret),
            "openai_configured": bool(s.openai_api_key),
            "verification_provider": s.verification_provider,
            "neverbounce_configured": bool(s.neverbounce_api_key),
            "sandbox_enabled": s.sandbox_enabled,
            "redis_configured": bool(s.redis_url),
            "app_env": s.app_env,
            "public_url": s.public_url,
        },
        "compliance_problems": app_settings.compliance_ready(db),
    }


@router.put("/{section}")
def update_section(section: str, body: dict, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    try:
        value = app_settings.update_section(db, section, body)
    except (app_settings.SettingsError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(db, user, "settings.updated", "settings", section, request, fields=sorted(body))
    db.commit()
    return value
