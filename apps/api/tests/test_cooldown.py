"""Cross-campaign cooldown: a prospect on two overlapping lists never gets two cold emails close together."""

from datetime import timedelta

from sqlalchemy import func, select, update

from app.models import CampaignContact, Contact, ScheduledMessage, SentMessage
from app.models.base import utcnow
from app.services import sending
from tests.test_sending import make_contacts, outbox, setup_campaign, setup_mailbox  # noqa: F401 (fixture)

ONE_STEP = [{"subject": "Hi {{first_name}}", "body": "Hi {{first_name}}, are you taking new clients?"}]


def _sent_to(db, email: str) -> int:
    return db.scalar(select(func.count()).select_from(SentMessage).where(SentMessage.to_email == email))


def test_second_campaign_waits_out_the_cooldown(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    shared, only_a, only_b = make_contacts(client, "shared@acmebail.com", "a@acmebail.com", "b@acmebail.com")
    setup_campaign(client, mid, [shared, only_a], steps=ONE_STEP, name="List one")
    assert sending.send_due() == {"sent": 2}

    b = setup_campaign(client, mid, [shared, only_b], steps=ONE_STEP, name="List two")
    assert sending.send_due() == {"sent": 1, "deferred": 1}
    db.expire_all()
    assert _sent_to(db, "shared@acmebail.com") == 1 and _sent_to(db, "b@acmebail.com") == 1
    waiting = db.scalar(select(ScheduledMessage).where(ScheduledMessage.campaign_id == b,
                                                       ScheduledMessage.status == "queued"))
    assert waiting.last_error.startswith("cooldown: emailed by “List one” on ")
    first = db.scalar(select(SentMessage.sent_at).where(SentMessage.to_email == "shared@acmebail.com"))
    assert waiting.due_at == first + timedelta(days=14)
    cc = db.get(CampaignContact, waiting.campaign_contact_id)
    assert cc.status == "active" and cc.next_send_at == waiting.due_at  # delayed, not dropped

    # once the cooldown has passed, the email goes out
    db.execute(update(SentMessage).values(sent_at=SentMessage.sent_at - timedelta(days=15)))
    db.execute(update(ScheduledMessage).where(ScheduledMessage.id == waiting.id).values(due_at=utcnow()))
    db.commit()
    assert sending.send_due() == {"sent": 1}
    db.expire_all()
    assert _sent_to(db, "shared@acmebail.com") == 2


def test_two_campaigns_starting_together_send_one_email(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [shared] = make_contacts(client, "shared@acmebail.com")
    setup_campaign(client, mid, [shared], steps=ONE_STEP, name="List one")
    setup_campaign(client, mid, [shared], steps=ONE_STEP, name="List two")
    assert sending.send_due() == {"sent": 1, "deferred": 1}
    db.expire_all()
    assert _sent_to(db, "shared@acmebail.com") == 1


def test_cooldown_off_allows_both(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [shared] = make_contacts(client, "shared@acmebail.com")
    setup_campaign(client, mid, [shared], steps=ONE_STEP, name="List one", cross_campaign_cooldown_days=0)
    setup_campaign(client, mid, [shared], steps=ONE_STEP, name="List two", cross_campaign_cooldown_days=0)
    assert sending.send_due() == {"sent": 2}


def test_own_follow_ups_are_not_held_back(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [lead] = make_contacts(client, "owner@acmebail.com")
    steps = ONE_STEP + [{"delay_days": 2, "subject": "", "body": "Following up.", "reply_in_thread": True}]
    cid = setup_campaign(client, mid, [lead], steps=steps)
    assert sending.send_due() == {"sent": 1}
    db.execute(update(CampaignContact).where(CampaignContact.campaign_id == cid)
               .values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert sending.send_due() == {"sent": 1}


def test_send_dialog_and_enroll_preview_show_the_overlap(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    emailed, running, fresh = make_contacts(client, "emailed@acmebail.com", "running@acmebail.com", "new@acmebail.com")
    setup_campaign(client, mid, [emailed], steps=ONE_STEP, name="List one")
    assert sending.send_due() == {"sent": 1}
    # "running" is waiting in another active campaign that hasn't reached them yet
    other = setup_campaign(client, mid, [running], steps=ONE_STEP, name="Later list")
    db.execute(update(CampaignContact).where(CampaignContact.campaign_id == other)
               .values(next_send_at=utcnow() + timedelta(days=1)))
    db.commit()

    cid = setup_campaign(client, mid, [emailed, running, fresh], steps=ONE_STEP, name="List two", start=False)
    s = client.get(f"/api/campaigns/{cid}/recipients-summary").json()
    assert s["eligible"] == 3 and s["recently_contacted"] == 1 and s["in_other_campaigns"] == 1
    assert s["cooldown_days"] == 14

    draft = client.post("/api/campaigns", json={"name": "Draft", "mailbox_ids": [mid]}).json()["id"]
    client.put(f"/api/campaigns/{draft}/steps", json={"steps": ONE_STEP})
    r = client.post(f"/api/campaigns/{draft}/contacts", json={"contact_ids": [emailed, running, fresh], "dry_run": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["ready"] == 3
    assert body["summary"]["recently_contacted"] == 1 and body["summary"]["in_other_campaign"] == 1
    notes = {row["email"]: row["cooldown"] for row in body["leads"]}
    assert notes["new@acmebail.com"] is None
    assert notes["emailed@acmebail.com"]["kind"] == "recently_contacted"
    assert notes["emailed@acmebail.com"]["message"].startswith("Emailed by “List one” on ")
    assert notes["running@acmebail.com"]["message"] == "Also in “Later list”: emails stay 14 days apart"


def test_cooldown_setting_round_trips_and_copies(client, db):
    cid = client.post("/api/campaigns", json={"name": "C"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}").json()["cross_campaign_cooldown_days"] == 14
    assert client.patch(f"/api/campaigns/{cid}", json={"cross_campaign_cooldown_days": 30}).status_code == 200
    assert client.patch(f"/api/campaigns/{cid}", json={"cross_campaign_cooldown_days": -1}).status_code == 422
    copy = client.post(f"/api/campaigns/{cid}/duplicate").json()
    assert copy["cross_campaign_cooldown_days"] == 30


def test_prospect_outreach_history(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [lead] = make_contacts(client, "owner@acmebail.com")
    setup_campaign(client, mid, [lead], steps=ONE_STEP, name="List one", cross_campaign_cooldown_days=0)
    setup_campaign(client, mid, [lead], steps=ONE_STEP, name="List two", cross_campaign_cooldown_days=0)
    assert sending.send_due() == {"sent": 2}

    h = client.get(f"/api/contacts/{lead}/outreach").json()
    assert {c["name"] for c in h["campaigns"]} == {"List one", "List two"}
    assert all(c["status"] == "completed" and c["emails_sent"] == 1 and c["total_steps"] == 1 for c in h["campaigns"])
    newest, oldest = h["emails"]
    assert oldest["too_close"] is False and oldest["gap_days"] is None and oldest["step"] == 1
    # same day as the other campaign's email: flagged, even though both campaigns had the cooldown off
    assert newest["too_close"] is True and newest["gap_days"] == 0
    assert newest["previous_campaign_name"] == oldest["campaign_name"]

    db.execute(update(Contact).values(deleted_at=utcnow()))
    db.commit()
    assert client.get(f"/api/contacts/{lead}/outreach").status_code == 404
