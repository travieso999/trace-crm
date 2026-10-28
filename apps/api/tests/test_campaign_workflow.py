"""Campaign workflow: a whole list as the audience, initial email + follow-ups on the same audience, replies and
unsubscribes excluded, draft/paused/scheduled follow-ups, send-now to non-responders, per-prospect pause/resume/
stop, bulk actions, history and analytics. Everything sends through the sandbox (nothing leaves the machine)."""

import pathlib
import shutil
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models import Campaign, CampaignContact, Contact, ScheduledMessage, SentMessage, SuppressionEntry
from app.models.base import utcnow
from app.services import sending
from app.workers import engine
from tests.test_import_lists import texas_rows, upload, xlsx
from tests.test_sending import ALWAYS, due_now, sent_files, setup_mailbox


@pytest.fixture
def outbox():
    root = pathlib.Path(get_settings().sandbox_outbox_dir)
    shutil.rmtree(root, ignore_errors=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


STEPS = [
    {"name": "Initial outreach", "subject": "Quick question for {{company_name}}",
     "body": "Hi {{first_name}},\n\nAre you taking new clients in {{city}}?"},
    {"name": "Follow-up #1", "delay_days": 3, "body": "Hi {{first_name}}, following up.", "reply_in_thread": True},
    {"name": "Final follow-up", "delay_days": 4, "body": "Last note, {{first_name}}.", "reply_in_thread": True},
]


def list_campaign(client, db, n=6, steps=STEPS, start=True):
    """Upload n Texas prospects as a list and build a campaign from the whole list."""
    upload(client, xlsx(texas_rows(n)), list_name="Texas Bail Bonds")
    batch_id = client.get("/api/imports").json()[0]["id"]
    mid = setup_mailbox(client, db)
    r = client.post("/api/campaigns", json={
        "name": "Texas Bail Bonds", "campaign_type": "Cold outreach", "target_location": "Texas",
        "industry": "Bail Bonds", "mailbox_ids": [mid], "schedule": {**ALWAYS, "use_recipient_timezone": False}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": steps}).status_code == 200
    client.put("/api/settings/compliance", json={"physical_address": "123 Main St, Suite 4, Austin, TX 78701"})
    assert client.post(f"/api/campaigns/{cid}/contacts", json={"batch_id": batch_id}).json()["enrolled"] == n
    if start:
        r = client.post(f"/api/campaigns/{cid}/start")
        assert r.status_code == 200, r.text
    return cid, mid


def step_ids(client, cid):
    return [s["id"] for s in client.get(f"/api/campaigns/{cid}").json()["steps"]]


def contact_id(db, email):
    return str(db.scalar(select(Contact.id).where(Contact.email == email)))


def test_campaign_details_are_saved(client, db, outbox):
    cid, _ = list_campaign(client, db, n=2, start=False)
    c = client.get(f"/api/campaigns/{cid}").json()
    assert (c["campaign_type"], c["target_location"], c["industry"]) == ("Cold outreach", "Texas", "Bail Bonds")
    assert [s["name"] for s in c["steps"]] == ["Initial outreach", "Follow-up #1", "Final follow-up"]
    assert c["import_lists"][0]["name"] == "Texas Bail Bonds"


def test_follow_ups_share_the_audience_and_skip_repliers_and_unsubscribed(client, db, outbox):
    cid, mid = list_campaign(client, db, n=4)
    assert sending.send_due() == {"sent": 4}  # initial email to the whole list
    s1, s2, s3 = step_ids(client, cid)
    assert client.get(f"/api/campaigns/{cid}/steps/{s2}/audience").json()["waiting"] == 4

    # owner0 replies, owner1 unsubscribes via the link
    sent0 = db.scalar(select(SentMessage).where(SentMessage.to_email == "owner0@txbail0.com"))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent0.id), "body": "Yes!"})
    db.add(SuppressionEntry(email="owner1@txbail1.com", reason="unsubscribe", source="link"))
    db.execute(CampaignContact.__table__.update().where(
        CampaignContact.contact_id == contact_id(db, "owner1@txbail1.com")).values(status="unsubscribed"))
    db.commit()

    aud = client.get(f"/api/campaigns/{cid}/steps/{s2}/audience").json()
    assert aud["waiting"] == 2 and aud["excluded"]["replied"] == 1 and aud["excluded"]["unsubscribed"] == 1
    summary = client.get(f"/api/campaigns/{cid}/recipients-summary").json()
    assert summary["total"] == 4 and summary["replied"] == 1 and summary["unsubscribed"] == 1
    assert summary["eligible"] == 2 and summary["missing_data"] == 0

    due_now(db, cid)
    assert sending.send_due() == {"sent": 2}
    due_now(db, cid)
    assert sending.send_due() == {"sent": 2}
    to = [m["To"] for m in sent_files(outbox)]
    assert to.count("owner0@txbail0.com") == 1 and to.count("owner1@txbail1.com") == 1  # initial email only
    assert to.count("owner2@txbail2.com") == 3
    # follow-ups stay in the thread
    thread = [m for m in sent_files(outbox) if m["To"] == "owner2@txbail2.com"]
    assert thread[1]["In-Reply-To"] == thread[0]["Message-ID"] and thread[2]["Subject"].startswith("Re: ")

    due_now(db, cid)
    assert not sending.send_due().get("sent")  # sequence finished: never a duplicate
    assert db.scalar(select(func.count()).select_from(ScheduledMessage).where(ScheduledMessage.status == "sent")) == 8


def test_draft_and_paused_follow_ups_hold_prospects_without_skipping(client, db, outbox):
    steps = [STEPS[0], {**STEPS[1], "status": "draft"}, STEPS[2]]
    cid, _ = list_campaign(client, db, n=2, steps=steps)
    assert sending.send_due() == {"sent": 2}
    due_now(db, cid)
    assert not sending.send_due().get("sent")  # follow-up #1 is a draft: everyone waits at it
    report = client.get(f"/api/campaigns/{cid}/steps-report").json()
    assert [r["state"] for r in report] == ["sent", "draft", "waiting"] and report[1]["waiting"] == 2

    current = client.get(f"/api/campaigns/{cid}").json()["steps"]
    current[1]["status"] = "active"
    assert client.put(f"/api/campaigns/{cid}/steps", json={"steps": current}).status_code == 200
    assert sending.send_due() == {"sent": 2}  # follow-up #1, not the final one
    assert db.scalar(select(func.max(CampaignContact.current_step))) == 2


def test_follow_up_on_a_set_date_waits_until_then(client, db, outbox):
    later = (utcnow() + timedelta(days=2)).isoformat()
    cid, _ = list_campaign(client, db, n=2, steps=[STEPS[0], {**STEPS[1], "delay_days": 0, "send_at": later}])
    assert sending.send_due() == {"sent": 2}
    due_now(db, cid)
    assert not sending.send_due().get("sent")
    assert sending.send_due(utcnow() + timedelta(days=2, minutes=1)).get("sent") == 2


def test_send_follow_up_now_to_non_responders_once(client, db, outbox):
    cid, mid = list_campaign(client, db, n=3)
    _, s2, _ = step_ids(client, cid)
    assert sending.send_due() == {"sent": 3}
    sent0 = db.scalar(select(SentMessage).where(SentMessage.to_email == "owner0@txbail0.com"))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent0.id)})
    r = client.post(f"/api/campaigns/{cid}/steps/{s2}/send-now")
    assert r.status_code == 200 and r.json()["queued"] == 2
    assert sending.send_due() == {"sent": 2}
    # a second click (or a retry) sends nothing more
    client.post(f"/api/campaigns/{cid}/steps/{s2}/send-now")
    assert not sending.send_due().get("sent")
    rows = client.get(f"/api/campaigns/{cid}/steps/{s2}/messages").json()
    assert sorted(r["email"] for r in rows) == ["owner1@txbail1.com", "owner2@txbail2.com"]


def test_campaign_pause_resume_does_not_release_a_backlog(client, db, outbox):
    cid, _ = list_campaign(client, db, n=2)
    assert sending.send_due() == {"sent": 2}
    next_before = db.scalar(select(func.min(CampaignContact.next_send_at)))
    assert client.post(f"/api/campaigns/{cid}/pause").status_code == 200
    db.execute(Campaign.__table__.update().values(paused_at=utcnow() - timedelta(days=5)))  # paused 5 days ago
    db.commit()
    assert client.post(f"/api/campaigns/{cid}/resume").status_code == 200
    db.expire_all()
    shifted = db.scalar(select(func.min(CampaignContact.next_send_at)))
    assert shifted - next_before >= timedelta(days=4, hours=23)  # follow-ups moved by the pause length
    assert not sending.send_due().get("sent")


def test_pause_resume_and_stop_individual_prospects(client, db, outbox):
    cid, mid = list_campaign(client, db, n=3)
    assert sending.send_due() == {"sent": 3}
    p0, p1 = contact_id(db, "owner0@txbail0.com"), contact_id(db, "owner1@txbail1.com")
    bulk = lambda **kw: client.post(f"/api/campaigns/{cid}/contacts/bulk", json=kw)  # noqa: E731
    assert bulk(action="pause", contact_ids=[p0]).json()["affected"] == 1
    assert bulk(action="stop", contact_ids=[p1]).json()["affected"] == 1
    due_now(db, cid)
    assert sending.send_due() == {"sent": 1}  # only owner2
    # a paused prospect who replies is stopped, so resuming never emails them again
    sent0 = db.scalar(select(SentMessage).where(SentMessage.to_email == "owner0@txbail0.com"))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent0.id)})
    assert bulk(action="resume", contact_ids=[p0]).json()["affected"] == 0
    st = {r["email"]: r["status"] for r in client.get(f"/api/campaigns/{cid}/contacts").json()["items"]}
    assert st == {"owner0@txbail0.com": "replied", "owner1@txbail1.com": "stopped", "owner2@txbail2.com": "active"}


def test_paused_prospect_resumes_later_not_immediately(client, db, outbox):
    cid, _ = list_campaign(client, db, n=1)
    assert sending.send_due() == {"sent": 1}
    p0 = contact_id(db, "owner0@txbail0.com")
    client.post(f"/api/campaigns/{cid}/contacts/bulk", json={"action": "pause", "contact_ids": [p0]})
    db.execute(CampaignContact.__table__.update().values(paused_at=utcnow() - timedelta(days=10)))
    db.commit()
    client.post(f"/api/campaigns/{cid}/contacts/bulk", json={"action": "resume", "contact_ids": [p0]})
    assert not sending.send_due().get("sent")  # their follow-up was due during the pause: it moved later


def test_remove_move_copy_export_and_tag_keep_prospects(client, db, outbox):
    cid, _ = list_campaign(client, db, n=4, start=False)
    other = client.post("/api/campaigns", json={"name": "Texas round 2"}).json()["id"]
    ids = [contact_id(db, f"owner{i}@txbail{i}.com") for i in range(4)]
    bulk = lambda **kw: client.post(f"/api/campaigns/{cid}/contacts/bulk", json=kw).json()  # noqa: E731

    assert bulk(action="remove", contact_ids=ids[:1])["affected"] == 1
    assert client.get(f"/api/campaigns/{cid}/contacts").json()["total"] == 3  # hidden from the campaign...
    assert client.get("/api/contacts").json()["total"] == 4  # ...still in Prospects
    assert bulk(action="copy", contact_ids=ids[1:2], target_campaign_id=other)["affected"] == 1
    r = bulk(action="move", contact_ids=ids[2:3], target_campaign_id=other)
    assert r["affected"] == 1 and r["removed_from_source"] == 1
    assert client.get(f"/api/campaigns/{other}/contacts").json()["total"] == 2
    assert client.get(f"/api/campaigns/{cid}/contacts").json()["total"] == 2

    assert bulk(action="add_tag", all_matching=True, tag="texas-q4")["affected"] == 2
    assert client.get("/api/contacts/count?tag=texas-q4").json()["total"] == 2
    csv = client.get(f"/api/campaigns/{cid}/contacts/export")
    assert csv.status_code == 200 and csv.text.count("@txbail") == 2

    # adding a removed prospect back on purpose works
    assert client.post(f"/api/campaigns/{cid}/contacts", json={"contact_ids": ids[:1]}).json()["enrolled"] == 1


def test_dynamic_audience_adds_new_matches_but_not_removed(client, db, outbox):
    cid, _ = list_campaign(client, db, n=2, start=False)
    r = client.patch(f"/api/campaigns/{cid}", json={"audience_mode": "dynamic",
                                                    "audience_filter": {"state": "TX", "industry": "Bail Bonds"}})
    assert r.status_code == 200 and r.json()["audience_mode"] == "dynamic"
    p0 = contact_id(db, "owner0@txbail0.com")
    client.post(f"/api/campaigns/{cid}/contacts/bulk", json={"action": "remove", "contact_ids": [p0]})
    client.post(f"/api/campaigns/{cid}/start")
    upload(client, xlsx(texas_rows(2, start=10)), name="more.xlsx")  # two new Texas bail bond prospects
    assert engine.sync_audiences() == 2
    emails = {r["email"] for r in client.get(f"/api/campaigns/{cid}/contacts").json()["items"]}
    assert emails == {"owner1@txbail1.com", "owner10@txbail10.com", "owner11@txbail11.com"}
    assert engine.sync_audiences() == 0
    # a dynamic audience needs a filter
    other = client.post("/api/campaigns", json={"name": "x"}).json()["id"]
    assert client.patch(f"/api/campaigns/{other}", json={"audience_mode": "dynamic"}).status_code == 422


def test_history_and_analytics_use_stated_denominators(client, db, outbox):
    cid, mid = list_campaign(client, db, n=4)
    assert sending.send_due() == {"sent": 4}
    sent0 = db.scalar(select(SentMessage).where(SentMessage.to_email == "owner0@txbail0.com"))
    sent1 = db.scalar(select(SentMessage).where(SentMessage.to_email == "owner1@txbail1.com"))
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate",
                json={"sent_message_id": str(sent0.id), "body": "Sounds good, let's talk"})
    client.post(f"/api/email-accounts/{mid}/sandbox/simulate", json={"sent_message_id": str(sent1.id), "kind": "bounce"})
    due_now(db, cid)
    assert sending.send_due() == {"sent": 2}

    a = client.get(f"/api/campaigns/{cid}/analytics").json()
    assert a["total_contacts"] == 4 and a["contacts_emailed"] == 4
    assert a["emails_sent"] == 6 and a["followups_sent"] == 2 and a["bounced"] == 1 and a["delivered"] == 5
    assert a["replies"] == 1 and a["awaiting_followup"] == 2
    # an unsubscribe added through the suppression list counts too
    client.post("/api/suppressions", json={"emails": ["owner2@txbail2.com"], "reason": "unsubscribe"})
    assert client.get(f"/api/campaigns/{cid}/analytics").json()["unsubscribes"] == 1
    assert a["rates"]["reply_rate"] == 25.0 and a["rates"]["bounce_rate"] == round(100 / 6, 1)
    assert "opens" in a["definitions"]  # explicitly not tracked

    report = client.get(f"/api/campaigns/{cid}/steps-report").json()
    assert report[0]["sent"] == 4 and report[0]["replied"] == 1 and report[0]["bounced"] == 1
    assert report[1]["sent"] == 2 and report[1]["state"] == "sent" and report[2]["state"] == "scheduled"

    hist = client.get(f"/api/campaigns/{cid}/contacts/{contact_id(db, 'owner0@txbail0.com')}/history").json()
    assert [h["direction"] for h in hist] == ["out", "in"] and hist[0]["step"] == 1


def test_summary_leaves_out_prospects_missing_merge_data(client, db, outbox):
    cid, _ = list_campaign(client, db, n=3, start=False)
    db.execute(Contact.__table__.update().where(Contact.email == "owner0@txbail0.com").values(first_name=None))
    db.commit()
    s = client.get(f"/api/campaigns/{cid}/recipients-summary").json()
    assert s["eligible"] == 2 and s["missing_data"] == 1 and s["missing_variables"] == {"first_name": 1}
    # with fallbacks they are sendable again
    client.patch(f"/api/campaigns/{cid}", json={"missing_variable_policy": "fallback", "variable_fallbacks": {"first_name": "there"}})
    assert client.get(f"/api/campaigns/{cid}/recipients-summary").json()["eligible"] == 3
