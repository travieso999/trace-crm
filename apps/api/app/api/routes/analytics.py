import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import reader
from app.core.db import get_db
from app.models import EmailEvent, User
from app.models.base import utcnow
from app.services import analytics, app_settings

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("")
def get_analytics(
    days: int = Query(30, ge=1, le=365),
    campaign_id: uuid.UUID | None = None,
    mailbox_id: uuid.UUID | None = None,
    state: str | None = None,
    tag: str | None = None,
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    tz_name = app_settings.get_section(db, "defaults").get("timezone") or "America/New_York"
    start = utcnow() - timedelta(days=days)
    conds = analytics.event_filters(start, None, campaign_id, mailbox_id, state, tag)
    # one extra day so the oldest local-day bucket is complete
    series_conds = analytics.event_filters(start - timedelta(days=1), None, campaign_id, mailbox_id, state, tag)
    return {
        "timezone": tz_name,
        "days": days,
        "summary": analytics.summary(db, conds),
        "daily": analytics.daily_series(db, series_conds, days, tz_name,
                                        ("accepted", "replied", "positive_reply", "bounced", "unsubscribed", "failed")),
        "per_campaign": analytics.per_campaign(db, conds, limit=50),
        "per_mailbox": analytics.per_mailbox(db, conds),
        "funnel": analytics.funnel(db),
        "has_data": db.query(EmailEvent.id).filter(*conds).first() is not None,
    }
