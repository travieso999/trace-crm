"""Templates with a video, the default button text, and the actions on the campaigns list."""

from sqlalchemy import select

from app.models import CampaignContact, ScheduledMessage
from app.services import sending
from app.services.rendering import VideoLink, render_email
from tests.test_custom_thumbnail import LINK, picture, put_image, video_with_link
from tests.test_sending import make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401
from tests.test_video_attachments import make_mp4, upload


def template(aid=None, thumb=None, **kw):
    return {"name": "T", "subject": "Hi {{first_name}}", "body": "Hello\n\n{{video}}\n\nBest", "video_asset_id": aid,
            "video_preview": bool(aid), "video_attach": False, "video_thumbnail_id": thumb, "video_cta_text": None, **kw}


# --- the button ---------------------------------------------------------------------------------------------------------

def test_the_default_button_text_and_its_plain_text_version():
    r = render_email("S", "Hi", {}, video=VideoLink(LINK))
    assert ">Get More Clients Now!</a>" in r.html and "Watch Our" not in r.html
    assert f"Get More Clients Now! {LINK}" in r.text


def test_button_text_without_its_own_punctuation_keeps_the_colon():
    assert f"Watch the demo: {LINK}" in render_email("S", "Hi", {}, video=VideoLink(LINK, None, "Watch the demo")).text


def test_a_step_with_its_own_button_text_keeps_it(client, db, outbox, compliance):  # noqa: F811
    aid = video_with_link(client)
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"), steps=[
        {"subject": "Hi", "body": "Hi", "video_asset_id": aid, "video_preview": True, "video_cta_text": "Mine"}])
    assert sending.send_due() == {"sent": 1}
    [m] = sent_files(outbox)
    assert ">Mine</a>" in m.get_body(("html",)).get_content()


# --- templates ----------------------------------------------------------------------------------------------------------

def test_a_template_saves_its_video_thumbnail_and_button(client):
    aid = video_with_link(client)
    thumb = put_image(client, picture()).json()["id"]
    r = client.post("/api/templates", json=template(aid, thumb, video_attach=True, video_cta_text="  Get   More  "))
    assert r.status_code == 201, r.text
    t = client.get("/api/templates").json()[0]
    assert (t["video_asset_id"], t["video_thumbnail_id"], t["video_cta_text"]) == (aid, thumb, "Get More")
    assert t["video_preview"] and t["video_attach"]


def test_editing_a_template_can_change_or_drop_the_video(client):
    aid = video_with_link(client)
    tid = client.post("/api/templates", json=template(aid)).json()["id"]
    r = client.put(f"/api/templates/{tid}", json=template(aid, video_preview=False)).json()
    assert r["video_asset_id"] is None and not r["video_preview"]


def test_a_plain_template_still_works(client):
    r = client.post("/api/templates", json={"name": "Plain", "subject": "S", "body": "B"})
    assert r.status_code == 201 and r.json()["video_asset_id"] is None and not r.json()["video_preview"]


def test_a_template_cannot_point_at_a_video_or_thumbnail_that_does_not_exist(client):
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post("/api/templates", json=template(missing)).status_code == 422
    aid = video_with_link(client)
    assert client.post("/api/templates", json=template(aid, missing)).status_code == 422


def test_deleting_a_video_used_only_by_a_template_leaves_a_valid_template(client):
    aid = upload(client, make_mp4())["id"]
    tid = client.post("/api/templates", json=template(aid, video_preview=False, video_attach=True)).json()["id"]
    assert client.delete(f"/api/video-assets/{aid}").status_code == 200
    t = next(x for x in client.get("/api/templates").json() if x["id"] == tid)
    assert t["video_asset_id"] is None and not t["video_preview"] and not t["video_attach"]


def test_the_template_preview_places_the_video_and_needs_no_campaign(client):
    aid = video_with_link(client)
    r = client.post("/api/campaigns/preview", json={"subject": "S", "body": "Top\n\n{{video}}\n\nBottom", "video_asset_id": aid, "video_preview": True}).json()
    assert r["ok"] and r["html"].index("Top") < r["html"].index("Get More Clients Now!") < r["html"].index("Bottom")


def test_the_template_preview_uses_a_mailbox_signature(client, db):
    mid = setup_mailbox(client, db)
    client.patch(f"/api/email-accounts/{mid}", json={"signature_text": "Sam Smith"})
    r = client.post("/api/campaigns/preview", json={"subject": "S", "body": "Hi\n\n{{signature}}"}).json()
    assert "Sam Smith" in r["html"]


# --- relaunch -----------------------------------------------------------------------------------------------------------

def finished(client, db, *emails):
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, *emails), steps=[{"subject": "Hi", "body": "Hi {{first_name}}"}])
    assert client.post(f"/api/campaigns/{cid}/stop").status_code == 200
    return cid, mid


def test_relaunch_makes_a_draft_with_the_same_emails_and_prospects_and_sends_nothing(client, db, outbox, compliance):  # noqa: F811
    cid, mid = finished(client, db, "a@acme.com", "b@acme.com")
    r = client.post(f"/api/campaigns/{cid}/relaunch")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["status"] == "draft" and copy["name"] == "TX bail bonds (relaunch)" and copy["id"] != cid
    assert copy["steps"][0]["body"] == "Hi {{first_name}}" and copy["mailbox_ids"] == [mid] and copy["stats"]["leads"] == 2
    assert sending.send_due() == {}
    assert sent_files(outbox) == [] and not db.scalars(select(ScheduledMessage).where(ScheduledMessage.status == "sent")).first()
    assert client.get(f"/api/campaigns/{cid}").json()["status"] == "stopped"


def test_relaunch_leaves_out_people_who_replied_unsubscribed_or_bounced(client, db, compliance):
    cid, _ = finished(client, db, "keep@acme.com", "replied@acme.com", "unsub@acme.com", "bounced@acme.com", "removed@acme.com")
    for email_, status in (("replied", "replied"), ("unsub", "unsubscribed"), ("bounced", "bounced"), ("removed", "removed")):
        contact_id = next(c["contact_id"] for c in client.get(f"/api/campaigns/{cid}/contacts").json()["items"] if c["email"].startswith(email_))
        db.execute(CampaignContact.__table__.update().where(CampaignContact.campaign_id == cid, CampaignContact.contact_id == contact_id).values(status=status))
    db.commit()
    copy = client.post(f"/api/campaigns/{cid}/relaunch").json()
    assert copy["stats"]["leads"] == 1
    assert [c["email"] for c in client.get(f"/api/campaigns/{copy['id']}/contacts").json()["items"]] == ["keep@acme.com"]


def test_relaunching_a_relaunch_does_not_stack_names(client, db, compliance):
    cid, _ = finished(client, db, "a@acme.com")
    copy = client.post(f"/api/campaigns/{cid}/relaunch").json()
    assert client.post(f"/api/campaigns/{copy['id']}/start").status_code == 200
    assert client.post(f"/api/campaigns/{copy['id']}/stop").status_code == 200
    assert client.post(f"/api/campaigns/{copy['id']}/relaunch").json()["name"] == "TX bail bonds (relaunch)"


def test_a_relaunched_draft_can_be_started_and_really_sends(client, db, outbox, compliance):  # noqa: F811
    cid, _ = finished(client, db, "a@acme.com")
    copy = client.post(f"/api/campaigns/{cid}/relaunch").json()
    assert client.post(f"/api/campaigns/{copy['id']}/start").status_code == 200
    assert sending.send_due() == {"sent": 1}
    assert len(sent_files(outbox)) == 1


def test_only_a_finished_campaign_can_be_relaunched(client, db, compliance):
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"))
    assert client.post(f"/api/campaigns/{cid}/relaunch").status_code == 409
    assert len(client.get("/api/campaigns").json()) == 1
