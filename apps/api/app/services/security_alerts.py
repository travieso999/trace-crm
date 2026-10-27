"""Security alerts for admins, delivered through the Notification Center (kind "security").

Alerts are for events an admin should look at: a locked-out sign-in, a new admin, two-step verification turned off,
a bulk export, a mailbox connected or disconnected, a user hitting permission errors repeatedly. Each alert is also
in the audit log (the caller writes that entry); the notification is only the nudge.
The admin who caused an event is alerted too: if their session was stolen, that is how they find out.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.models import Notification, User
from app.models.base import utcnow

THROTTLE_SECONDS = 600


def notify_admins(db: Session, title: str, body: str, *, throttle_key: str | None = None, **data) -> int:
    """Adds one notification per active admin. With `throttle_key`, repeats within 10 minutes are dropped."""
    if throttle_key and not ratelimit.hit(f"security_alert:{throttle_key}", 1, THROTTLE_SECONDS):
        return 0
    now = utcnow()
    admins = db.scalars(select(User).where(User.role == "admin", User.is_active.is_(True)))
    n = 0
    for admin in admins:
        db.add(Notification(user_id=admin.id, kind="security", title=title[:300], body=body, fire_at=now,
                            data={"security": True, **data}))
        n += 1
    return n
