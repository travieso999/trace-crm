"""RFC 5322 message construction shared by every adapter."""

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.providers.base import OutgoingMessage


def new_message_id(from_email: str) -> str:
    domain = from_email.rsplit("@", 1)[-1] or "trace.local"
    return make_msgid(domain=domain)


def build_mime(msg: OutgoingMessage) -> EmailMessage:
    m = EmailMessage()
    m["From"] = formataddr((msg.from_name or "", msg.from_email))
    m["To"] = msg.to_email
    m["Subject"] = msg.subject
    m["Date"] = formatdate(localtime=False, usegmt=True)
    m["Message-ID"] = msg.rfc_message_id
    if msg.reply_to:
        m["Reply-To"] = msg.reply_to
    if msg.in_reply_to:
        m["In-Reply-To"] = msg.in_reply_to
    if msg.references:
        m["References"] = " ".join(msg.references)
    if msg.list_unsubscribe_url:
        # RFC 8058 one-click unsubscribe
        m["List-Unsubscribe"] = f"<{msg.list_unsubscribe_url}>"
        m["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    m.set_content(msg.text)
    m.add_alternative(msg.html, subtype="html")
    return m
