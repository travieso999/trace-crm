import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, reader, writer
from app.core.db import get_db
from app.models import SuppressionEntry, User
from app.schemas.common import Message, Page
from app.schemas.mail import SuppressionIn, SuppressionOut
from app.services import campaigns as campaign_service
from app.services.normalize import InvalidEmail, normalize_email

router = APIRouter(prefix="/api/suppressions", tags=["suppressions"])

STOP_STATUS = {"unsubscribe": "unsubscribed", "reply_request": "unsubscribed", "bounce": "bounced"}


@router.get("", response_model=Page[SuppressionOut])
def list_suppressions(
    q: str | None = None,
    reason: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _: User = Depends(reader),
    db: Session = Depends(get_db),
):
    stmt = select(SuppressionEntry)
    if q:
        stmt = stmt.where(func.lower(SuppressionEntry.email).like(f"%{q.strip().lower()}%"))
    if reason:
        stmt = stmt.where(SuppressionEntry.reason == reason)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = list(db.scalars(stmt.order_by(SuppressionEntry.created_at.desc(), SuppressionEntry.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("")
def add_suppressions(body: SuppressionIn, request: Request, user: User = Depends(writer), db: Session = Depends(get_db)):
    added, existing, invalid = 0, 0, []
    stopped = 0
    seen: set[str] = set()
    for raw in body.emails:
        try:
            email_addr = normalize_email(raw)
        except InvalidEmail:
            if raw.strip():
                invalid.append(raw.strip()[:320])
            continue
        if email_addr in seen:
            continue
        seen.add(email_addr)
        if db.scalar(select(SuppressionEntry.id).where(SuppressionEntry.email == email_addr)):
            existing += 1
            continue
        db.add(SuppressionEntry(email=email_addr, reason=body.reason, source="manual", created_by=user.id))
        stopped += campaign_service.stop_email_everywhere(db, email_addr, f"suppressed_{body.reason}",
                                                          STOP_STATUS.get(body.reason, "stopped"))
        added += 1
    audit(db, user, "suppression.added", "suppression", None, request, added=added, reason=body.reason)
    db.commit()
    return {"added": added, "already_suppressed": existing, "invalid": invalid[:50], "enrollments_stopped": stopped}


@router.delete("/{entry_id}", response_model=Message)
def remove_suppression(entry_id: uuid.UUID, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    e = db.get(SuppressionEntry, entry_id)
    if e is None:
        raise HTTPException(404, "Entry not found")
    audit(db, user, "suppression.removed", "suppression", e.id, request, email=e.email, reason=e.reason)
    db.delete(e)
    db.commit()
    return Message(message=f"{e.email} can be contacted again")
