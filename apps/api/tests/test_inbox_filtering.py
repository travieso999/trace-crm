"""Unified Inbox shows only campaign replies; conversations can be soft-deleted and stay hidden across syncs."""

import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import pytest
from sqlalchemy import select

from app.models import CampaignContact, Contact, Conversation, EmailEvent, ReceivedMessage, SentMessage, SuppressionEntry
from app.models.base import utcnow
from app.providers.sandbox import SandboxProvider
from app.services import inbox, sending
from tests.test_sending import MAILBOX, make_contacts, outbox, setup_campaign, setup_mailbox  # noqa: F401

pytestmark = pytest.mark.usefixtures("outbox")  # clean sandbox outbox per test


def deliver(mid_client, mid, *, sender, subject, body, thread=None, in_reply_to=None, references=None, **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, MAILBOX, subject, make_msgid(domain="example.com")
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    SandboxProvider(MAILBOX).deliver_inbound(m.as_bytes(), thread)
    return mid_client.post(f"/api/email-accounts/{mid}/sync").json()


def campaign_with_one_send(client, db, lead="owner@acmebail.com"):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, lead)
    cid = setup_campaign(client, mid, [contact_id])
    assert sending.send_due() == {"sent": 1}
    return mid, cid, contact_id, db.scalar(select(SentMessage))


def test_only_campaign_replies_reach_the_inbox(client, db, compliance):
    mid, cid, contact_id, sent = campaign_with_one_send(client, db)

    # unrelated mail: a newsletter, a personal email, and personal mail from someone who is also a lead
    assert deliver(client, mid, sender="news@shop.example", subject="50% off", body="Big sale",
                   List_Unsubscribe="<https://shop.example/u>", Precedence="bulk") == {"processed": 0}
    assert deliver(client, mid, sender="friend@gmail.com", subject="Dinner?", body="Are we still on?") == {"processed": 0}
    [other] = make_contacts(client, "personal@acmebail.com")
    assert deliver(client, mid, sender="personal@acmebail.com", subject="Photos", body="Here they are") == {"processed": 0}
    # a bounce of a personal email must not suppress anyone
    assert deliver(client, mid, sender="MAILER-DAEMON@mail.example", subject="Undeliverable: hi",
                   body="Delivery failed") == {"processed": 0}
    assert db.scalar(select(ReceivedMessage)) is None and db.scalar(select(SuppressionEntry)) is None
    assert client.get("/api/inbox").json()["total"] == 0

    # a genuine reply matched only by In-Reply-To / References (no provider thread id)
    r = deliver(client, mid, sender="Ann <owner@acmebail.com>", subject="Re: Quick question", body="Sounds good, call me.",
                in_reply_to=sent.rfc_message_id, references=sent.rfc_message_id)
    assert r == {"processed": 1}
    items = client.get("/api/inbox").json()["items"]
    assert len(items) == 1 and items[0]["contact"]["id"] == contact_id and items[0]["campaign"] == "TX bail bonds"
    db.expire_all()
    assert db.scalar(select(CampaignContact.status).where(CampaignContact.campaign_id == cid)) == "replied"
    assert {e.type for e in db.scalars(select(EmailEvent))} >= {"replied", "positive_reply"}

    # a reply in the campaign's provider thread also matches
    r = deliver(client, mid, sender="owner@acmebail.com", subject="Re: Quick question", body="Also, Tuesday works.",
                thread=sent.provider_thread_id)
    assert r == {"processed": 1}
    assert client.get("/api/inbox").json()["total"] == 1  # same conversation thread
    assert db.get(Contact, other).lead_status == "new_lead"  # personal mail changed nothing for that lead


def test_direct_email_from_enrolled_lead_is_hidden_but_still_stops_and_unsubscribes(client, db, compliance):
    mid = setup_mailbox(client, db)
    a, b = make_contacts(client, "a@acmebail.com", "b@acmebail.com")
    cid = setup_campaign(client, mid, [a, b])
    assert sending.send_due() == {"sent": 2}

    assert deliver(client, mid, sender="a@acmebail.com", subject="Different topic", body="Call me about the bond.") \
        == {"processed": 0}
    assert deliver(client, mid, sender="b@acmebail.com", subject="hello", body="Please remove me from your list.") \
        == {"processed": 0}
    assert client.get("/api/inbox").json()["total"] == 0 and db.scalar(select(ReceivedMessage)) is None
    db.expire_all()
    statuses = dict(db.execute(select(Contact.email, CampaignContact.status).join(
        CampaignContact, CampaignContact.contact_id == Contact.id).where(CampaignContact.campaign_id == cid)).all())
    assert statuses == {"a@acmebail.com": "replied", "b@acmebail.com": "unsubscribed"}
    assert db.scalar(select(SuppressionEntry.email)) == "b@acmebail.com"
    assert not db.scalar(select(EmailEvent).where(EmailEvent.type == "replied"))  # not counted as a campaign reply


def test_reply_to_a_test_send_is_not_a_campaign_reply(client, db, compliance):
    mid = setup_mailbox(client, db)
    r = client.post("/api/campaigns/test-send", json={"subject": "Hi", "body": "Test", "email_account_id": mid,
                                                       "to_email": "admin@trace.studio"})
    assert r.status_code == 200, r.text
    test_sent = db.scalar(select(SentMessage))
    assert test_sent.kind == "test"
    r = client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(test_sent.id)})
    assert r.json() == {"processed": 0}
    assert client.get("/api/inbox").json()["total"] == 0


def test_delete_hides_conversation_and_survives_resync(client, db, compliance):
    mid = setup_mailbox(client, db)
    a, b = make_contacts(client, "a@acmebail.com", "b@acmebail.com")
    setup_campaign(client, mid, [a, b])
    sending.send_due()
    for s in db.scalars(select(SentMessage)):
        client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(s.id), "body": "Interested"})
    inbox_page = client.get("/api/inbox").json()
    assert inbox_page["total"] == 2 and inbox_page["unread"] == 2
    first, second = (i["id"] for i in inbox_page["items"])

    r = client.post("/api/inbox/delete", json={"conversation_ids": [first]})
    assert r.json() == {"deleted": 1, "unread": 1}
    page = client.get("/api/inbox").json()
    assert page["total"] == 1 and page["items"][0]["id"] == second
    assert client.get(f"/api/inbox/{first}").status_code == 404
    assert client.get("/api/inbox/unread-count").json() == {"unread": 1}
    assert client.post("/api/inbox/delete", json={"conversation_ids": [first]}).json()["deleted"] == 0  # idempotent

    # kept in the database: conversation, messages, events and audit trail
    db.expire_all()
    conv = db.get(Conversation, first)
    assert conv.hidden_reason == "deleted" and conv.hidden_by_user_id is not None
    assert db.scalar(select(ReceivedMessage).where(ReceivedMessage.conversation_id == conv.id))
    replied_before = len(list(db.scalars(select(EmailEvent).where(EmailEvent.type == "replied"))))

    # another reply in the deleted thread is still tracked but the conversation stays hidden
    sent_a = db.scalar(select(SentMessage).where(SentMessage.conversation_id == conv.id))
    assert deliver(client, mid, sender=sent_a.to_email, subject="Re: again", body="Any update?",
                   thread=sent_a.provider_thread_id) == {"processed": 1}
    assert [i["id"] for i in client.get("/api/inbox").json()["items"]] == [second]
    assert len(list(db.scalars(select(EmailEvent).where(EmailEvent.type == "replied")))) == replied_before + 1

    # bulk delete
    assert client.post("/api/inbox/delete", json={"conversation_ids": [first, second]}).json()["deleted"] == 1
    assert client.get("/api/inbox").json()["total"] == 0
    assert client.post("/api/inbox/delete", json={"conversation_ids": []}).status_code == 422


def test_cleanup_hides_previously_synced_unrelated_mail(client, db, compliance):
    mid, cid, contact_id, sent = campaign_with_one_send(client, db)
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent.id), "body": "Yes"})
    # what the old sync stored for personal mail: an "unmatched" conversation with its message
    legacy = Conversation(email_account_id=uuid.UUID(mid), provider_thread_id="gmail-personal-1", subject="Dinner?",
                          has_inbound=True, is_read=False, last_message_at=utcnow())
    db.add(legacy)
    db.flush()
    db.add(ReceivedMessage(email_account_id=uuid.UUID(mid), conversation_id=legacy.id, provider_message_id="p-1",
                           provider_thread_id="gmail-personal-1", from_email="friend@gmail.com", subject="Dinner?",
                           body_text="Are we still on?", kind="unmatched", received_at=utcnow()))
    db.commit()
    assert client.get("/api/inbox").json()["total"] == 2

    assert inbox.hide_unrelated(db, apply=False) == {"would_hide": 1, "conversation_ids": [str(legacy.id)]}
    db.rollback()
    assert client.get("/api/inbox").json()["total"] == 2  # dry run changed nothing
    assert inbox.hide_unrelated(db, apply=True)["hidden"] == 1
    db.commit()
    items = client.get("/api/inbox").json()["items"]
    assert len(items) == 1 and items[0]["contact"]["id"] == contact_id
    db.expire_all()
    assert db.get(Conversation, legacy.id).hidden_reason == "not_campaign_reply"
    assert db.scalar(select(ReceivedMessage).where(ReceivedMessage.conversation_id == legacy.id))  # data kept
    assert inbox.hide_unrelated(db, apply=True)["hidden"] == 0  # idempotent
