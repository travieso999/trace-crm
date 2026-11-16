"""Notification Center notice when an address is added to the suppression list (kind "suppression").

Recipients: every active admin, plus the opportunity owner when the address belongs to an assigned prospect.
Automatic additions (unsubscribe link, unsubscribe reply, bounce, complaint) give one notice per address. A manual
paste of many addresses gives one summary, and the person who added them is not notified about their own action.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Contact, CrmOpportunity, Notification, User
from app.models.base import utcnow

REASONS = {
    "unsubscribe": "Unsubscribed",
    "reply_request": "Asked to be removed",
    "bounce": "Bounced",
    "complaint": "Marked as spam",
    "manual": "Added by hand",
}


def _admins(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(select(User.id).where(User.role == "admin", User.is_active.is_(True), User.deleted_at.is_(None))))


def _send(db: Session, users: set[uuid.UUID], title: str, body: str, contact_id: uuid.UUID | None, **data) -> int:
    now = utcnow()
    for uid in users:
        db.add(Notification(user_id=uid, kind="suppression", title=title[:300], body=body, contact_id=contact_id,
                            fire_at=now, data=data))
    return len(users)


def notify_added(db: Session, email: str, reason: str, source: str | None, *, actor: User | None = None) -> int:
    """One address was suppressed. Returns how many people were notified."""
    contact = db.scalar(select(Contact).where(Contact.email == email, Contact.deleted_at.is_(None)))
    users = set(_admins(db))
    if contact:
        owner = db.scalar(select(CrmOpportunity.owner_id).where(CrmOpportunity.contact_id == contact.id))
        if owner and db.scalar(select(User.id).where(User.id == owner, User.is_active.is_(True))):
            users.add(owner)
    if actor:
        users.discard(actor.id)
    name = " ".join(p for p in (contact.first_name, contact.last_name) if p) if contact else ""
    who = f"{name} ({email})" if name else email
    label = REASONS.get(reason, reason.replace("_", " ").capitalize())
    details = [label]
    if contact and contact.company_name:
        details.append(contact.company_name)
    if source and source != "manual":
        details.append(source)
    if actor:
        details.append(f"by {actor.display_name}")
    return _send(db, users, f"Added to the suppression list: {who}", " · ".join(details),
                 contact.id if contact else None, email=email, reason=reason)


def notify_bulk(db: Session, emails: list[str], reason: str, actor: User) -> int:
    """Several addresses suppressed by hand in one go: a single summary instead of one notice each."""
    if not emails:
        return 0
    if len(emails) == 1:
        return notify_added(db, emails[0], reason, "manual", actor=actor)
    users = set(_admins(db)) - {actor.id}
    shown = ", ".join(emails[:5]) + (f" and {len(emails) - 5} more" if len(emails) > 5 else "")
    return _send(db, users, f"{len(emails)} addresses added to the suppression list",
                 f"{REASONS.get(reason, reason)} by {actor.display_name}: {shown}", None, count=len(emails), reason=reason)
