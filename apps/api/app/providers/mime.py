"""RFC 5322 message construction shared by every adapter."""

from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from app.providers.base import OutgoingMessage, PermanentProviderError

# Largest whole message per provider, attachments included after encoding. Gmail's published attachment limit is 25 MB.
# A password mailbox can't be asked in advance, so it gets the same common limit and the server's own SIZE when sending.
# The sandbox mirrors Gmail so that a passing .eml test means the same as a real send.
MESSAGE_LIMITS = {"gmail": 25_000_000, "smtp": 25_000_000, "sandbox": 25_000_000}
UNKNOWN_PROVIDER_LIMIT = 20_000_000
# room kept for headers, the text and HTML parts and an inline thumbnail (512 KB is about 700 KB once encoded)
MESSAGE_OVERHEAD = 800_000


def message_limit(provider: str) -> int:
    return MESSAGE_LIMITS.get(provider, UNKNOWN_PROVIDER_LIMIT)


def encoded_size(size: int) -> int:
    """Bytes an attachment takes in the message: base64 grows it by 4/3, plus a CRLF every 76 characters."""
    b64 = (size + 2) // 3 * 4
    return b64 + (b64 + 75) // 76 * 2


def max_attachment_bytes(provider: str) -> int:
    """The largest file that still fits this provider's message limit once encoded."""
    room = message_limit(provider) - MESSAGE_OVERHEAD
    size = room * 76 // 78 * 3 // 4
    while encoded_size(size) > room:
        size -= 1
    return size


def check_message_size(provider: str, raw: bytes) -> None:
    limit = message_limit(provider)
    if len(raw) > limit:
        raise PermanentProviderError(f"The message is {len(raw):,} bytes; {provider} accepts at most {limit:,}")


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
    if msg.inline_images:
        html_part = m.get_payload()[1]
        for img in msg.inline_images:
            maintype, subtype = img.content_type.split("/", 1)
            html_part.add_related(img.data, maintype=maintype, subtype=subtype, cid=f"<{img.cid}>")
    for a in msg.attachments:  # turns the message into multipart/mixed, with a standard base64 file part
        maintype, subtype = a.content_type.split("/", 1)
        m.add_attachment(a.data, maintype=maintype, subtype=subtype, filename=a.filename)
    return m
