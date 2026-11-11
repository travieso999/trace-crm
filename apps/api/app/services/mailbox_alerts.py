"""Mailbox health alerts: tell the admins when a mailbox stops working, once per failure.

A mailbox whose password or Google access is rejected is marked ``auth_error``. Sending and reply sync skip it until
an admin reconnects it, so it fails quietly unless someone looks at Email Accounts; this puts it in the bell.
"""

from sqlalchemy.orm import Session

from app.models import EmailAccount
from app.models.base import utcnow
from app.services import security_alerts


def mark_auth_error(db: Session, account: EmailAccount, message: str) -> bool:
    """Record a rejected sign-in for the mailbox. Alerts the admins only when it newly fails. Returns that."""
    newly = account.status != "auth_error"
    account.status, account.last_error, account.last_error_at = "auth_error", message[:2000], utcnow()
    if newly:
        security_alerts.notify_admins(
            db, f"Mailbox needs reconnecting: {account.email}",
            f"The mail server rejected the sign-in for {account.email} ({message[:200]}). Sending and reply sync from "
            "this mailbox are paused until you reconnect it in Email Accounts.",
            kind="mailbox", email_account_id=str(account.id))
    return newly
