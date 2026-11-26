"""Prospects left out for missing data rejoin by themselves, import previews, Needs attention, the daily summary email,
company suggestions and the bulk-add message."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.models import Campaign, CampaignContact
from app.models.base import utcnow
from app.services import attention, company_suggest, daily_summary, notify_email, sending
from tests.test_import_enroll import _upload
from tests.test_sending import make_contacts, outbox, setup_campaign, setup_mailbox  # noqa: F401

ONE_STEP = [{"subject": "Quick question", "body": "Hi {{first_name}},\n\nAre you taking new clients?"}]


def _nameless(client, email="info@lonestarbail.com") -> str:
    r = client.post("/api/contacts", json={"email": email, "company_name": "Lone Star Bail"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _cc(db, cid, contact_id):
    db.expire_all()
    return db.scalar(select(CampaignContact).where(CampaignContact.campaign_id == cid, CampaignContact.contact_id == contact_id))


def test_a_prospect_left_out_rejoins_once_the_name_is_filled_in(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [named] = make_contacts(client, "jane@doebail.com")
    nameless = _nameless(client)
    cid = setup_campaign(client, mid, [named], steps=ONE_STEP)
    r = client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": [nameless]}).json()
    assert r["skipped_missing_fields"] == 1
    cc = _cc(db, cid, nameless)
    assert (cc.status, cc.stop_reason) == ("removed", "missing_data")
    # waiting isn't counted as a prospect of the campaign, but Needs attention lists it
    assert client.get(f"/api/campaigns/{cid}").json()["stats"]["leads"] == 1
    assert any(i["kind"] == "missing_data" for i in client.get("/api/dashboard").json()["attention"])

    assert client.patch(f"/api/contacts/{nameless}", json={"first_name": "Maria"}).status_code == 200
    assert _cc(db, cid, nameless).status == "active"
    assert sending.send_due() == {"sent": 2}
    assert not any(i["kind"] == "missing_data" for i in client.get("/api/dashboard").json()["attention"])


def test_a_finished_campaign_reopens_for_a_prospect_whose_name_is_filled_in(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [named] = make_contacts(client, "jane@doebail.com")
    nameless = _nameless(client)
    cid = setup_campaign(client, mid, [named], steps=ONE_STEP)
    client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": [nameless]})
    assert sending.send_due() == {"sent": 1}
    sending.send_due()  # the next pass sees nobody left and completes it
    db.expire_all()
    assert db.get(Campaign, cid).status == "completed"

    # filled in through an upload: back in, and the campaign reopens for them
    r = _upload(client, b"Email,First Name\ninfo@lonestarbail.com,Maria\n", "filled.csv", duplicate_strategy="update")
    assert r.status_code == 200, r.text
    assert r.json()["readded"]["readded"] == 1 and r.json()["readded"]["campaigns"][0]["reopened"] is True
    db.expire_all()
    assert db.get(Campaign, cid).status == "active"
    assert sending.send_due() == {"sent": 1}


def test_import_dry_run_shows_who_would_join_and_who_is_left_out(client, db):
    cid = client.post("/api/campaigns", json={"name": "Kentucky"}).json()["id"]
    client.put(f"/api/campaigns/{cid}/steps", json={"steps": ONE_STEP})
    sheet = b"Name,Company,Email\nJane Doe,Doe Bail,jane@doebail.com\nLone Star Bail Bonds LLC,Lone Star,info@lonestarbail.com\n"
    r = _upload(client, sheet, "kentucky.csv", campaign_id=cid, dry_run=True)
    assert r.status_code == 200, r.text
    p = r.json()["enrollment_preview"]
    assert (p["ready"], p["missing_fields"]) == (1, 1)
    assert client.get("/api/contacts/count", params={"q": "doebail"}).json()["total"] == 0  # nothing saved
    assert db.scalar(select(CampaignContact.id)) is None


def test_stuck_prospects_are_flagged(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [lead] = make_contacts(client, "jane@doebail.com")
    cid = setup_campaign(client, mid, [lead], steps=ONE_STEP)
    db.execute(CampaignContact.__table__.update().values(next_send_at=utcnow() - timedelta(days=2)))
    db.commit()
    [stuck] = [i for i in attention.items(db) if i["kind"] == "stuck"]
    assert stuck["count"] == 1 and stuck["href"] == f"/campaigns/{cid}" and stuck["severity"] == "error"
    assert sending.send_due() == {"sent": 1}
    assert not any(i["kind"] == "stuck" for i in attention.items(db))


def test_daily_summary_sends_once_a_day_after_the_hour(client, db, outbox, compliance, monkeypatch):  # noqa: F811
    sent = []
    monkeypatch.setattr(daily_summary, "get_settings",
                        lambda: SimpleNamespace(notify_email_configured=True, public_url="https://crm.example"))
    monkeypatch.setattr(notify_email, "send", lambda to, subject, text: sent.append((to, subject, text)))
    client.put("/api/settings/notifications", json={"daily_summary_email": "Owner@Example.com"})
    wednesday = datetime(2026, 12, 2, 23, 0, tzinfo=UTC)  # 6 PM in New York, the default time zone
    assert daily_summary.tick(wednesday - timedelta(hours=2)) is False  # before 5 PM
    assert daily_summary.tick(wednesday) is True
    assert daily_summary.tick(wednesday + timedelta(minutes=30)) is False  # once a day
    [(to, subject, text)] = sent
    assert to == "owner@example.com" and subject.startswith("Trace CRM daily summary: 0 sent")
    assert "Not sent today:" in text and "Needs attention:" in text and "https://crm.example/" in text
    assert daily_summary.tick(datetime(2026, 12, 5, 23, 0, tzinfo=UTC)) is False  # Saturday: not a sending day
    assert daily_summary.tick(datetime(2026, 12, 3, 23, 0, tzinfo=UTC)) is True  # the next weekday


def test_company_name_suggestions():
    assert company_suggest.suggest("anbba@americannationalbail.com") == "American National Bail"
    assert company_suggest.suggest("info@lonestarbail.com") == "Lone Star Bail"
    assert company_suggest.suggest("x@247bailbonds.com") == "247 Bail Bonds"
    assert company_suggest.suggest("someone@gmail.com") is None
    assert company_suggest.suggest("not-an-email") is None


def test_missing_company_gets_a_suggestion_and_using_it_rejoins(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    lead = client.post("/api/contacts", json={"email": "george@americannationalbail.com", "first_name": "George"}).json()["id"]
    steps = [{"subject": "For {{company_name}}", "body": "Hi {{first_name}}, a question."}]
    [other] = make_contacts(client, "jane@doebail.com")
    cid = setup_campaign(client, mid, [other], steps=steps)
    client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": [lead]})
    [row] = client.get("/api/contacts", params={"missing_data": "true"}).json()["items"]
    assert row["missing_fields"] == ["company_name"] and row["suggested_company"] == "American National Bail"
    client.patch(f"/api/contacts/{lead}", json={"company_name": row["suggested_company"]})
    assert _cc(db, cid, lead).status == "active"


def test_bulk_add_says_who_was_skipped(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [named] = make_contacts(client, "jane@doebail.com")
    nameless = _nameless(client)
    cid = setup_campaign(client, mid, [], steps=ONE_STEP, start=False)
    r = client.post("/api/contacts/bulk", json={"ids": [named, nameless], "action": "assign_campaign", "value": cid}).json()
    assert r["updated"] == 1 and "1 missing a name or company" in r["message"]
