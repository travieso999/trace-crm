"""Mail provider adapter interface.

Adapters translate between our outgoing/inbound message model and a provider API (Gmail, Microsoft
Graph, or the local Sandbox). They never touch the database; the sending engine owns all state.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class ProviderError(Exception):
    """Base class. ``retryable`` errors are retried with backoff by the sender."""

    retryable = False


class TemporaryProviderError(ProviderError):
    retryable = True


class PermanentProviderError(ProviderError):
    pass


class ProviderAuthError(ProviderError):
    """Token revoked/expired beyond refresh: the mailbox needs to be reconnected."""


class AmbiguousSendError(ProviderError):
    """The request may or may not have been accepted (e.g. timeout after upload). Reconcile later."""


class ProviderUnavailable(ProviderError):
    """No adapter for this provider in this deployment (e.g. OAuth not configured)."""


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class InlineImage:
    """Shown in the HTML part through ``cid:<cid>``."""

    cid: str
    content_type: str
    data: bytes


@dataclass
class OutgoingMessage:
    from_email: str
    from_name: str | None
    to_email: str
    subject: str
    text: str
    html: str
    rfc_message_id: str  # "<...@...>"
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    provider_thread_id: str | None = None
    reply_to: str | None = None
    list_unsubscribe_url: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    inline_images: list[InlineImage] = field(default_factory=list)


@dataclass
class SendResult:
    provider_message_id: str
    provider_thread_id: str | None


@dataclass
class InboundRaw:
    provider_message_id: str
    provider_thread_id: str | None
    raw: bytes
    received_at: datetime | None = None


class MailProvider(Protocol):
    def send(self, msg: OutgoingMessage) -> SendResult: ...

    def find_sent(self, rfc_message_id: str) -> SendResult | None:
        """Look up a previously sent message by our Message-ID (used to reconcile ambiguous sends)."""
        ...

    def fetch_new(self, cursor: str | None) -> tuple[list[InboundRaw], str | None]:
        """Inbound messages since ``cursor``; returns the new cursor."""
        ...
