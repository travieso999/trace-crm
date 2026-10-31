"""Development-only provider: nothing leaves the machine.

Outgoing mail is written to ``<SANDBOX_OUTBOX_DIR>/<mailbox>/sent/*.eml``. Inbound mail is read from
``<SANDBOX_OUTBOX_DIR>/<mailbox>/inbox/*.eml`` (written by the "simulate reply" endpoint), so simulated
replies go through exactly the same sync → parse → match pipeline as real ones.
"""

import email
import pathlib
import re
import uuid
from datetime import UTC, datetime
from email import policy

from app.core.config import get_settings
from app.providers.base import InboundRaw, OutgoingMessage, SendResult
from app.providers.mime import build_mime, check_message_size

THREAD_HEADER = "X-Sandbox-Thread-Id"


def _safe(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9@._-]", "_", name)


def mailbox_dir(mailbox_email: str) -> pathlib.Path:
    return pathlib.Path(get_settings().sandbox_outbox_dir) / _safe(mailbox_email.lower())


class SandboxProvider:
    def __init__(self, mailbox_email: str):
        self.root = mailbox_dir(mailbox_email)

    def send(self, msg: OutgoingMessage) -> SendResult:
        thread_id = msg.provider_thread_id or f"sbx-{uuid.uuid4().hex}"
        mime = build_mime(msg)
        mime[THREAD_HEADER] = thread_id
        out = self.root / "sent"
        out.mkdir(parents=True, exist_ok=True)
        provider_id = f"sbx-{uuid.uuid4().hex}"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        raw = mime.as_bytes()
        if msg.attachments:
            check_message_size("sandbox", raw)
        (out / f"{stamp}_{provider_id}.eml").write_bytes(raw)
        return SendResult(provider_message_id=provider_id, provider_thread_id=thread_id)

    def find_sent(self, rfc_message_id: str) -> SendResult | None:
        for path in sorted((self.root / "sent").glob("*.eml")):
            m = email.message_from_bytes(path.read_bytes(), policy=policy.default)
            if (m.get("Message-ID") or "").strip() == rfc_message_id:
                return SendResult(provider_message_id=path.stem.split("_", 1)[1], provider_thread_id=m.get(THREAD_HEADER))
        return None

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundRaw], str | None]:
        inbox = self.root / "inbox"
        if not inbox.exists():
            return [], cursor
        files = sorted(p for p in inbox.glob("*.eml") if cursor is None or p.name > cursor)
        out = []
        for p in files:
            raw = p.read_bytes()
            m = email.message_from_bytes(raw, policy=policy.default)
            out.append(InboundRaw(provider_message_id=p.stem.split("_", 1)[-1], provider_thread_id=m.get(THREAD_HEADER),
                                  raw=raw))
        return out, (files[-1].name if files else cursor)

    # --- dev helper -----------------------------------------------------------------------------
    def deliver_inbound(self, raw: bytes, thread_id: str | None) -> str:
        m = email.message_from_bytes(raw, policy=policy.default)
        if thread_id:
            del m[THREAD_HEADER]
            m[THREAD_HEADER] = thread_id
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        provider_id = f"sbx-in-{uuid.uuid4().hex}"
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        (inbox / f"{stamp}_{provider_id}.eml").write_bytes(m.as_bytes())
        return provider_id
