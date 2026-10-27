"""Plain SMTP for reminder emails to the operator's own notification address.

Deliberately separate from the campaign mail providers: it has no access to Gmail tokens, mailboxes, leads or
templates, so a reminder can never become a marketing email. Configured with NOTIFY_SMTP_* settings.
"""

import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings


class NotifyEmailError(Exception):
    pass


def send(to: str, subject: str, text: str) -> None:
    s = get_settings()
    if not s.notify_email_configured:
        raise NotifyEmailError("NOTIFY_SMTP_HOST and NOTIFY_SMTP_FROM are not set")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.notify_smtp_from, to, subject.replace("\n", " ")[:200]
    msg["Auto-Submitted"] = "auto-generated"  # tells mail servers not to auto-reply
    msg.set_content(text)
    try:
        with smtplib.SMTP(s.notify_smtp_host, s.notify_smtp_port, timeout=20) as smtp:
            if s.notify_smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.notify_smtp_username:
                smtp.login(s.notify_smtp_username, s.notify_smtp_password or "")
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        raise NotifyEmailError(f"{type(exc).__name__}: {exc}") from exc
