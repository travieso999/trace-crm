"""Unified Inbox visibility. Hiding a conversation only takes it out of the inbox: its messages, events, lead
activity and analytics are kept, nothing is deleted at the mail provider, and later syncs keep it hidden."""

import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.models import Conversation, ReceivedMessage, SentMessage, User
from app.models.base import utcnow
from app.services.inbound import campaign_mail

HIDE_REASONS = ("deleted", "not_campaign_reply")


def visible():
    """Conversations the Unified Inbox shows."""
    return Conversation.has_inbound.is_(True), Conversation.hidden_at.is_(None)


def hide(db: Session, conversations: list[Conversation], reason: str, user: User | None = None) -> list[Conversation]:
    """Hide the given conversations that are not hidden yet; returns those it hid."""
    assert reason in HIDE_REASONS
    now = utcnow()
    hidden = [c for c in conversations if c.hidden_at is None]
    for c in hidden:
        c.hidden_at, c.hidden_reason, c.hidden_by_user_id = now, reason, user.id if user else None
    db.flush()
    return hidden


def is_campaign_conversation():
    """A conversation that contains campaign mail (in the conversation or its provider thread) or a received message
    answering campaign mail. Mirrors the matching rules in services.inbound.match for mail synced before them."""
    sent_here = exists().where(campaign_mail(), or_(
        SentMessage.conversation_id == Conversation.id,
        (SentMessage.email_account_id == Conversation.email_account_id)
        & (SentMessage.provider_thread_id == Conversation.provider_thread_id),
    ))
    answers_campaign_mail = exists().where(
        ReceivedMessage.conversation_id == Conversation.id,
        ReceivedMessage.in_reply_to.in_(select(SentMessage.rfc_message_id).where(campaign_mail())),
    )
    return or_(sent_here, answers_campaign_mail)


def unrelated_conversations(db: Session) -> list[Conversation]:
    """Visible conversations that are not campaign conversations (mail synced before replies were filtered)."""
    return list(db.scalars(select(Conversation).where(*visible(), ~is_campaign_conversation())
                           .order_by(Conversation.last_message_at)))


def hide_unrelated(db: Session, apply: bool) -> dict:
    """Hide visible non-campaign conversations. With apply=False only reports what would be hidden."""
    convs = unrelated_conversations(db)
    ids = [str(c.id) for c in convs]
    if apply:
        hide(db, convs, "not_campaign_reply")
    return {"would_hide" if not apply else "hidden": len(ids), "conversation_ids": ids}


def by_ids(db: Session, ids: list[uuid.UUID]) -> list[Conversation]:
    return list(db.scalars(select(Conversation).where(Conversation.id.in_(ids)))) if ids else []
