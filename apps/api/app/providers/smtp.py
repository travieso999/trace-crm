"""SMTP + IMAP adapter for mailboxes without an API (Hostinger, Zoho, cPanel hosts, ...).

Sends over SMTP with a password, keeps a copy in the IMAP Sent folder (best effort, so it shows in webmail) and reads
replies from the IMAP INBOX without marking them read. The sync cursor is ``<UIDVALIDITY>:<last UID>``; the first sync
starts from "now", like Gmail. SMTP has no thread ids: replies are matched by In-Reply-To/References.

Sending is refused unless the caller was granted it by the REAL_SENDING policy (see providers.get_provider).
"""

import email.utils
import imaplib
import logging
import re
import smtplib
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from app.providers.base import (
    AmbiguousSendError,
    InboundRaw,
    OutgoingMessage,
    PermanentProviderError,
    ProviderAuthError,
    ProviderUnavailable,
    SendResult,
    TemporaryProviderError,
)
from app.providers.mime import build_mime, check_message_size

logger = logging.getLogger("app.providers.smtp")

TIMEOUT = 30
MAX_FETCH = 100  # per sync pass; the rest comes on the next pass
SENT_FALLBACKS = ("Sent", "INBOX.Sent", "Sent Items", "Sent Messages", "INBOX.Sent Items")
SECURITY = ("ssl", "starttls")

PRESETS = {
    "hostinger": {"smtp_host": "smtp.hostinger.com", "smtp_port": 465, "smtp_security": "ssl",
                  "imap_host": "imap.hostinger.com", "imap_port": 993},
}

_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


@dataclass
class ServerConfig:
    smtp_host: str
    smtp_port: int
    smtp_security: str  # ssl (implicit TLS, usually 465) | starttls (usually 587)
    imap_host: str
    imap_port: int  # implicit TLS
    username: str
    password: str

    @classmethod
    def from_account(cls, server: dict, password: str) -> "ServerConfig":
        return cls(smtp_host=server["smtp_host"], smtp_port=int(server["smtp_port"]),
                   smtp_security=server.get("smtp_security", "ssl"), imap_host=server["imap_host"],
                   imap_port=int(server["imap_port"]), username=server["username"], password=password)


class SmtpImapProvider:
    def __init__(self, from_email: str, config: ServerConfig, *, send_block_reason: Callable[[str], str | None]):
        """``send_block_reason(to_email)`` is asked before every send; a non-None answer refuses that message."""
        self.from_email = from_email
        self.cfg = config
        self.send_block_reason = send_block_reason

    # --- connections ------------------------------------------------------------------------------

    def _smtp(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        try:
            if self.cfg.smtp_security == "ssl":
                conn = smtplib.SMTP_SSL(self.cfg.smtp_host, self.cfg.smtp_port, timeout=TIMEOUT, context=ctx)
            else:
                conn = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=TIMEOUT)
                conn.starttls(context=ctx)
            conn.login(self.cfg.username, self.cfg.password)
        except smtplib.SMTPAuthenticationError as exc:
            raise ProviderAuthError("The mail server rejected the username or password (SMTP)") from exc
        except (smtplib.SMTPException, OSError) as exc:
            raise TemporaryProviderError(f"Could not connect to {self.cfg.smtp_host}:{self.cfg.smtp_port} "
                                         f"({type(exc).__name__})") from exc
        return conn

    def _imap(self) -> imaplib.IMAP4_SSL:
        try:
            conn = imaplib.IMAP4_SSL(self.cfg.imap_host, self.cfg.imap_port, timeout=TIMEOUT,
                                     ssl_context=ssl.create_default_context())
        except OSError as exc:
            raise TemporaryProviderError(f"Could not connect to {self.cfg.imap_host}:{self.cfg.imap_port} "
                                         f"({type(exc).__name__})") from exc
        try:
            conn.login(self.cfg.username, self.cfg.password)
        except imaplib.IMAP4.error as exc:
            _logout(conn)
            raise ProviderAuthError("The mail server rejected the username or password (IMAP)") from exc
        except OSError as exc:
            raise TemporaryProviderError(f"IMAP login failed ({type(exc).__name__})") from exc
        return conn

    def verify(self) -> None:
        """Log in to SMTP and IMAP once; raises the same errors as sending/syncing would."""
        _quit(self._smtp())
        _logout(self._imap())

    # --- MailProvider -----------------------------------------------------------------------------

    def send(self, msg: OutgoingMessage) -> SendResult:
        if blocked := self.send_block_reason(msg.to_email if msg else ""):
            raise ProviderUnavailable(blocked)
        mime = build_mime(msg)
        raw = mime.as_bytes()
        if msg.attachments:
            check_message_size("smtp", raw)
        conn = self._smtp()
        try:
            if msg.attachments:
                _check_server_size(conn, len(raw))
            # Envelope first: a refusal here means nothing was sent, so it is safe to retry or fail outright
            try:
                _check(conn.mail(self.from_email))
                _check(conn.rcpt(msg.to_email))
            except (smtplib.SMTPServerDisconnected, OSError) as exc:
                raise TemporaryProviderError(f"SMTP connection dropped ({type(exc).__name__})") from exc
            # From DATA on the server may have accepted the message even if we never see its answer
            try:
                code, resp = conn.data(raw)
            except smtplib.SMTPResponseException as exc:
                _check((exc.smtp_code, exc.smtp_error))
                raise
            except (smtplib.SMTPServerDisconnected, OSError) as exc:
                raise AmbiguousSendError(f"No answer from the SMTP server after the message ({type(exc).__name__})") from exc
            _check((code, resp))
        finally:
            _quit(conn)
        # A copy per recipient would fill the mailbox quota with the same video: the Sent copy keeps only the email
        self._save_sent_copy(build_mime(replace(msg, attachments=[])).as_bytes() if msg.attachments else raw)
        return SendResult(provider_message_id=msg.rfc_message_id, provider_thread_id=None)

    def find_sent(self, rfc_message_id: str) -> SendResult | None:
        """SMTP cannot be asked whether a message went out. Only a send cut off after the whole message was handed
        over is ambiguous, and those are nearly always delivered, so it counts as sent: a missed follow-up is better
        than the same cold email twice."""
        return SendResult(provider_message_id=rfc_message_id, provider_thread_id=None)

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundRaw], str | None]:
        conn = self._imap()
        try:
            typ, _ = conn.select("INBOX", readonly=True)
            if typ != "OK":
                raise TemporaryProviderError("Could not open INBOX")
            validity = _first(conn.response("UIDVALIDITY")[1])
            uidnext = _status_uidnext(conn)
            start = _parse_cursor(cursor, validity)
            if start is None:  # first sync, or the server renumbered the mailbox: start from now
                return [], f"{validity}:{uidnext - 1}"
            typ, data = conn.uid("SEARCH", None, f"UID {start + 1}:*")
            uids = sorted(int(u) for u in (data[0] or b"").split() if int(u) > start)[:MAX_FETCH]
            out, last = [], start
            for uid in uids:
                typ, parts = conn.uid("FETCH", str(uid), "(INTERNALDATE BODY.PEEK[])")
                raw, received = _fetched(parts)
                last = uid
                if raw is None:
                    continue  # expunged between SEARCH and FETCH
                out.append(InboundRaw(provider_message_id=f"{validity}:{uid}", provider_thread_id=None, raw=raw,
                                      received_at=received))
            return out, f"{validity}:{last}"
        except (imaplib.IMAP4.error, OSError) as exc:
            raise TemporaryProviderError(f"IMAP sync failed ({type(exc).__name__}: {exc})") from exc
        finally:
            _logout(conn)

    # --- helpers ----------------------------------------------------------------------------------

    def _save_sent_copy(self, raw: bytes) -> None:
        try:
            conn = self._imap()
        except Exception:  # the message is already sent; the copy is a convenience
            logger.warning("smtp_sent_copy_failed", exc_info=True)
            return
        try:
            folder = _sent_folder(conn)
            if folder:
                conn.append(_quote(folder), r"(\Seen)", imaplib.Time2Internaldate(time.time()), raw)
        except Exception:
            logger.warning("smtp_sent_copy_failed", exc_info=True)
        finally:
            _logout(conn)


def _check(reply: tuple[int, bytes]) -> None:
    code, resp = reply
    if 200 <= code < 400:
        return
    text = f"SMTP {code}: {resp.decode(errors='replace') if isinstance(resp, bytes) else resp}"
    if 400 <= code < 500:
        raise TemporaryProviderError(text)
    raise PermanentProviderError(text)


def _check_server_size(conn: smtplib.SMTP, size: int) -> None:
    """Servers announce their limit in EHLO (SIZE, RFC 1870); refusing here beats a failed upload."""
    announced = getattr(conn, "esmtp_features", {}).get("size", "")
    if announced.isdigit() and 0 < int(announced) < size:
        raise PermanentProviderError(f"The message is {size:,} bytes; this mail server accepts at most {int(announced):,}")


def _quit(conn: smtplib.SMTP) -> None:
    try:
        conn.quit()
    except Exception:  # noqa: S110 - closing a connection we are done with
        pass


def _logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: S110
        pass


def _first(values: list | None) -> str:
    v = (values or [b""])[0]
    return v.decode() if isinstance(v, bytes) else str(v or "")


def _status_uidnext(conn: imaplib.IMAP4) -> int:
    typ, data = conn.status("INBOX", "(UIDNEXT)")
    m = re.search(rb"UIDNEXT (\d+)", data[0] or b"") if typ == "OK" else None
    if not m:
        raise TemporaryProviderError("The IMAP server did not report UIDNEXT")
    return int(m.group(1))


def _parse_cursor(cursor: str | None, validity: str) -> int | None:
    if not cursor or ":" not in cursor:
        return None
    v, last = cursor.split(":", 1)
    return int(last) if v == validity and last.isdigit() else None


def _fetched(parts: list) -> tuple[bytes | None, object]:
    for p in parts or []:
        if isinstance(p, tuple) and len(p) == 2:
            received = None
            if m := re.search(rb'INTERNALDATE "([^"]+)"', p[0]):
                received = email.utils.parsedate_to_datetime(_internaldate_to_rfc2822(m.group(1).decode()))
            return p[1], received
    return None, None


def _internaldate_to_rfc2822(value: str) -> str:
    # IMAP: "27-Oct-2026 16:31:12 +0000" -> RFC 2822: "27 Oct 2026 16:31:12 +0000"
    return value.replace("-", " ", 2)


def _sent_folder(conn: imaplib.IMAP4) -> str | None:
    typ, data = conn.list()
    names: list[str] = []
    for line in (data or []) if typ == "OK" else []:
        m = _LIST_RE.match(line.decode(errors="replace") if isinstance(line, bytes) else str(line))
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        if r"\Sent" in m.group("flags"):
            return name
        names.append(name)
    return next((n for n in SENT_FALLBACKS if n in names), None)


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
