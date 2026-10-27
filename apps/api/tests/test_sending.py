"""End-to-end sending engine tests: real Postgres, Sandbox provider (writes .eml files, sends nothing)."""

import email
import pathlib
import shutil
from datetime import timedelta
from email import policy

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import (
    CampaignContact,
    Contact,
    EmailAccount,
    EmailEvent,
    ScheduledMessage,
    SentMessage,
    SuppressionEntry,
)
from app.models.base import utcnow
from app.services import sending
from app.workers import engine
from tests.conftest import login, make_user

ALWAYS = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59", "timezone": "UTC"}
MAILBOX = "outreach@trace.studio"


@pytest.fixture
def outbox():
    root = pathlib.Path(get_settings().sandbox_outbox_dir)
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def sent_files(root: pathlib.Path) -> list[email.message.EmailMessage]:
    return [email.message_from_bytes(p.read_bytes(), policy=policy.default)
            for p in sorted((root / MAILBOX / "sent").glob("*.eml"))]


def setup_mailbox(client, db, **overrides) -> str:
    r = client.post("/api/email-accounts/sandbox", json={"email": MAILBOX, "display_name": "Sam at Trace"})
    assert r.status_code == 201, r.text
    mid = r.json()["id"]
    assert client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS, **overrides}).status_code == 200
    # spacing would otherwise defer every second message in a test
    db.execute(EmailAccount.__table__.update().values(min_interval_seconds=0))
    db.commit()
    return mid


def make_contacts(client, *emails) -> list[str]:
    ids = []
    for i, e in enumerate(emails):
        r = client.post("/api/contacts", json={"email": e, "first_name": f"Lead{i}", "company_name": f"Co {i}",
                                               "state": "TX", "tags": ["pilot"]})
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


STEPS = [
    {"subject": "Quick question for {{company_name}}", "body": "Hi {{first_name}},\n\nAre you taking new clients?"},
    {"delay_days": 2, "subject": "", "body": "Hi {{first_name}}, following up.", "reply_in_thread": True},
]


def setup_campaign(client, mailbox_id, contact_ids, steps=STEPS, start=True, **settings) -> str:
    r = client.post("/api/campaigns", json={"name": "TX bail bonds", "schedule": {**ALWAYS, "use_recipient_timezone": False},
                                            "mailbox_ids": [mailbox_id], **settings})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": steps}).status_code == 200
    assert client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": contact_ids}).json()["enrolled"] == len(contact_ids)
    if start:
        r = client.post(f"/api/campaigns/{cid}/start")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "active"
    return cid


def due_now(db, campaign_id):
    db.execute(CampaignContact.__table__.update().where(CampaignContact.campaign_id == campaign_id,
                                                        CampaignContact.status == "active")
               .values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()


def test_preflight_blocks_until_ready(client, db, outbox):
    r = client.post("/api/campaigns", json={"name": "Empty"})
    cid = r.json()["id"]
    r = client.post(f"/api/campaigns/{cid}/start")
    assert r.status_code == 409
    problems = r.json()["detail"]["problems"]
    assert any("physical postal address" in p for p in problems)
    assert any("connected email account" in p for p in problems)
    assert any("Enroll" in p for p in problems)
    assert any("empty body" in p for p in problems)


def test_full_sequence_threads_and_completes(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "owner@acmebail.com")
    cid = setup_campaign(client, mid, [contact_id])

    assert sending.send_due() == {"sent": 1}
    [first] = sent_files(outbox)
    assert first["To"] == "owner@acmebail.com"
    assert first["Subject"] == "Quick question for Co 0"
    assert "Hi Lead0," in first.get_body(("plain",)).get_content()
    assert first["List-Unsubscribe"].startswith("<http") and first["List-Unsubscribe-Post"]
    assert "123 Main St" in first.get_body(("plain",)).get_content()  # compliance footer

    db.expire_all()
    cc = db.scalar(select(CampaignContact).where(CampaignContact.campaign_id == cid))
    assert cc.current_step == 1 and cc.status == "active"
    assert cc.next_send_at > utcnow() + timedelta(hours=24)  # 2-day delay before step 2
    contact = db.get(Contact, cc.contact_id)
    assert contact.lead_status == "contacted" and contact.last_contacted_at
    assert {e.type for e in db.scalars(select(EmailEvent))} == {"attempted", "accepted"}
    assert sending.send_due() == {}  # nothing due yet

    due_now(db, cid)
    assert sending.send_due() == {"sent": 1}
    second = sent_files(outbox)[1]
    assert second["Subject"] == "Re: Quick question for Co 0"
    assert second["In-Reply-To"] == first["Message-ID"]
    assert second["X-Sandbox-Thread-Id"] == first["X-Sandbox-Thread-Id"]

    db.expire_all()
    assert db.get(CampaignContact, cc.id).status == "completed"
    camp = client.get(f"/api/campaigns/{cid}").json()
    assert camp["status"] == "completed" and camp["stats"]["sent"] == 2
    msgs = client.get(f"/api/campaigns/{cid}/messages").json()
    assert msgs["total"] == 2 and {m["status"] for m in msgs["items"]} == {"sent"}


def test_positive_reply_stops_followups_and_reaches_inbox(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "owner@acmebail.com")
    cid = setup_campaign(client, mid, [contact_id])
    sending.send_due()
    sent = db.scalar(select(SentMessage))

    r = client.post(f"/api/email-accounts/{mid}/sandbox/simulate",
                    json={"sent_message_id": str(sent.id), "body": "Sounds good, let's talk Tuesday."})
    assert r.status_code == 200 and r.json()["processed"] == 1, r.text

    db.expire_all()
    cc = db.scalar(select(CampaignContact).where(CampaignContact.campaign_id == cid))
    assert cc.status == "replied" and cc.next_send_at is None
    assert db.get(Contact, contact_id).lead_status == "interested"
    types = [e.type for e in db.scalars(select(EmailEvent))]
    assert "replied" in types and "positive_reply" in types

    due_now(db, cid)  # even if something tries to schedule it, the follow-up must not go out
    assert sending.send_due() == {}
    assert len(sent_files(outbox)) == 1

    inbox = client.get("/api/inbox").json()
    assert inbox["total"] == 1 and inbox["unread"] == 1
    conv = inbox["items"][0]
    assert conv["intent"] == "positive" and conv["snippet"].startswith("Sounds good")
    thread = client.get(f"/api/inbox/{conv['id']}").json()
    assert [m["direction"] for m in thread["messages"]] == ["out", "in"]

    r = client.post(f"/api/inbox/{conv['id']}/reply", json={"body": "Great, Tuesday 10am works."})
    assert r.status_code == 200, r.text
    reply = sent_files(outbox)[-1]
    assert reply["Subject"].startswith("Re:") and reply["In-Reply-To"]
    assert client.patch(f"/api/inbox/{conv['id']}", json={"is_read": True}).json()["is_read"] is True
    assert client.get("/api/inbox/unread-count").json()["unread"] == 0

    # re-syncing never duplicates a message
    assert client.post(f"/api/email-accounts/{mid}/sync").json()["processed"] == 0


def test_bounce_suppresses_and_invalidates(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "gone@acmebail.com")
    cid = setup_campaign(client, mid, [contact_id])
    sending.send_due()
    sent = db.scalar(select(SentMessage))
    r = client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent.id), "kind": "bounce"})
    assert r.json()["processed"] == 1, r.text
    db.expire_all()
    assert db.scalar(select(SuppressionEntry.reason).where(SuppressionEntry.email == "gone@acmebail.com")) == "bounce"
    assert db.scalar(select(CampaignContact.status).where(CampaignContact.campaign_id == cid)) == "bounced"
    assert db.get(Contact, contact_id).verification_status == "invalid"
    assert client.get("/api/inbox").json()["total"] == 0  # bounces don't clutter the inbox


def test_auto_reply_does_not_stop_by_default(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "ooo@acmebail.com")
    cid = setup_campaign(client, mid, [contact_id])
    sending.send_due()
    sent = db.scalar(select(SentMessage))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate",
                json={"sent_message_id": str(sent.id), "kind": "auto_reply", "body": "I am out of the office."})
    db.expire_all()
    assert db.scalar(select(CampaignContact.status).where(CampaignContact.campaign_id == cid)) == "active"
    assert db.scalar(select(EmailEvent.type).where(EmailEvent.type == "auto_reply"))


def test_unsubscribe_reply_and_link(client, app, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    a, b = make_contacts(client, "a@acmebail.com", "b@acmebail.com")
    cid = setup_campaign(client, mid, [a, b])
    assert sending.send_due() == {"sent": 2}
    sent_a = db.scalar(select(SentMessage).where(SentMessage.to_email == "a@acmebail.com"))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate",
                json={"sent_message_id": str(sent_a.id), "body": "Please remove me from your list."})

    from fastapi.testclient import TestClient

    public = TestClient(app)  # no session cookie
    url = sending.unsubscribe_url(b, cid).split("/u/", 1)[1]
    page = public.get(f"/u/{url}")
    assert page.status_code == 200 and "<form" in page.text and "b@acmebail.com" in page.text
    assert public.post(f"/u/{url}", data={"List-Unsubscribe": "One-Click"}).status_code == 200
    assert public.get("/u/not-a-token").status_code == 404

    db.expire_all()
    statuses = dict(db.execute(select(Contact.email, CampaignContact.status).join(
        CampaignContact, CampaignContact.contact_id == Contact.id)).all())
    assert statuses == {"a@acmebail.com": "unsubscribed", "b@acmebail.com": "unsubscribed"}
    reasons = dict(db.execute(select(SuppressionEntry.email, SuppressionEntry.reason)).all())
    assert reasons == {"a@acmebail.com": "reply_request", "b@acmebail.com": "unsubscribe"}
    # suppressed addresses can't be re-enrolled
    other = client.post("/api/campaigns", json={"name": "Second"}).json()["id"]
    r = client.post(f"/api/campaigns/{other}/contacts", json={"contact_ids": [a, b]}).json()
    assert r["skipped_suppressed"] == 2 and r["enrolled"] == 0


def test_missing_variable_blocks_send(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "owner@acmebail.com")
    steps = [{"subject": "Hi {{first_name}}", "body": "Saw your {{favorite_color}} logo."}]
    cid = setup_campaign(client, mid, [contact_id], steps=steps)
    pre = client.get(f"/api/campaigns/{cid}/preflight").json()
    assert pre["variables"] == {"leads_blocked": 1, "by_variable": {"favorite_color": 1}}
    assert sending.send_due() == {"missing_variables": 1}
    assert sent_files(outbox) == []
    db.expire_all()
    cc = db.scalar(select(CampaignContact))
    assert cc.status == "error" and cc.stop_reason == "missing_variables"


def test_fallback_policy_fills_missing_variable(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    [contact_id] = make_contacts(client, "owner@acmebail.com")
    steps = [{"subject": "Hi", "body": "Saw your {{favorite_color}} logo."}]
    setup_campaign(client, mid, [contact_id], steps=steps, missing_variable_policy="fallback",
                   variable_fallbacks={"favorite_color": "great"})
    assert sending.send_due() == {"sent": 1}
    assert "Saw your great logo." in sent_files(outbox)[0].get_body(("plain",)).get_content()


def test_daily_limit_and_window_defer(client, db, outbox, compliance):
    mid = setup_mailbox(client, db, daily_limit=1)
    ids = make_contacts(client, "a@acmebail.com", "b@acmebail.com")
    setup_campaign(client, mid, ids)
    assert sending.send_due() == {"sent": 1, "deferred": 1}
    db.expire_all()
    deferred = db.scalar(select(ScheduledMessage).where(ScheduledMessage.status == "queued"))
    assert deferred.last_error == "mailbox daily limit reached" and deferred.due_at > utcnow()

    # closed window: only sends on a weekday that isn't today
    today = utcnow().weekday()
    client.patch(f"/api/email-accounts/{mid}", json={"daily_limit": 50, "schedule": {**ALWAYS, "days": [(today + 1) % 7]}})
    db.execute(ScheduledMessage.__table__.update().values(due_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert sending.send_due() == {"deferred": 1}
    db.expire_all()
    assert db.scalar(select(ScheduledMessage.last_error).where(ScheduledMessage.status == "queued")) == \
        "outside sending window"


def test_pause_resume_and_stop(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    ids = make_contacts(client, "a@acmebail.com")
    cid = setup_campaign(client, mid, ids)
    assert client.post(f"/api/campaigns/{cid}/pause").json()["status"] == "paused"
    assert sending.send_due() == {}
    assert client.post(f"/api/campaigns/{cid}/resume").json()["status"] == "active"
    assert sending.send_due() == {"sent": 1}
    assert client.post(f"/api/campaigns/{cid}/stop").json()["status"] == "stopped"
    due_now(db, cid)
    assert sending.send_due() == {}
    assert client.patch(f"/api/campaigns/{cid}", json={"name": "x"}).status_code == 409
    dup = client.post(f"/api/campaigns/{cid}/duplicate").json()
    assert dup["status"] == "draft" and len(dup["steps"]) == 2 and dup["mailbox_ids"] == [mid]


def test_paused_mailbox_moves_new_leads_and_engine_tick(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    ids = make_contacts(client, "a@acmebail.com")
    setup_campaign(client, mid, ids)
    client.post(f"/api/email-accounts/{mid}/pause")
    result = engine.tick()
    assert result["sends"] == {} or result["sends"].get("deferred") is None
    assert sent_files(outbox) == []
    client.post(f"/api/email-accounts/{mid}/resume")
    db.execute(ScheduledMessage.__table__.update().values(due_at=utcnow() - timedelta(minutes=1)))
    db.execute(CampaignContact.__table__.update().values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert engine.tick()["sends"] == {"sent": 1}


def test_permissions(app, db, outbox, compliance):
    make_user("viewer@trace.studio", role="viewer")
    make_user("member@trace.studio", role="member")
    viewer = login(app, "viewer@trace.studio")
    member = login(app, "member@trace.studio")
    assert viewer.get("/api/campaigns").status_code == 200
    assert viewer.post("/api/campaigns", json={"name": "x"}).status_code == 403
    assert member.post("/api/campaigns", json={"name": "x"}).status_code == 201
    assert member.post("/api/email-accounts/sandbox", json={"email": "x@trace.studio"}).status_code == 403
    assert viewer.post("/api/suppressions", json={"emails": ["a@b.com"]}).status_code == 403


def test_test_send_allowlist_and_preview(client, db, outbox, compliance):
    mid = setup_mailbox(client, db)
    body = {"subject": "Hi {{first_name}}", "body": "Hello {{company_name}}", "email_account_id": mid}
    assert client.post("/api/campaigns/test-send", json={**body, "to_email": "stranger@gmail.com"}).status_code == 403
    r = client.post("/api/campaigns/test-send", json={**body, "to_email": "admin@trace.studio"})
    assert r.status_code == 200, r.text
    assert sent_files(outbox)[0]["Subject"] == "[TEST] Hi Alex"
    p = client.post("/api/campaigns/preview", json={"subject": "Hi {{first_name}}", "body": "{{nope}}"}).json()
    assert p["ok"] is False and p["missing"] == ["nope"]
    p = client.post("/api/campaigns/preview", json={"subject": "Hi {{first_name}}", "body": "Hello"}).json()
    assert p["ok"] and p["subject"] == "Hi Alex" and "Unsubscribe" in p["text"]
