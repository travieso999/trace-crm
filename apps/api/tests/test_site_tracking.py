"""Website tracking: batches trace-studio.tech forwards to POST /api/track/site-events (spec: tracking-events.md).

End to end: the email link carries the prospect's id, the website posts the spec's sample payload with the secret,
and the visit shows up on that prospect, in Engaged Prospects and in the CRM.
"""

import copy
import json
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.core.config import get_settings
from app.models import Contact, CrmActivity, Notification, SentMessage, SiteEvent, SiteVisitor, TrackedLink
from app.models.base import utcnow
from app.services import sending, site_tracking, tracking
from tests.test_sending import make_contacts, outbox, setup_campaign, setup_mailbox  # noqa: F401

SECRET = "test-webhook-secret-0123456789-abcdefghijklmnopqrstuvwxyz"
URL = "/api/track/site-events"

# the doc's sample payload, exactly as written in tracking-events.md section 3
SAMPLE = {
    "source": "trace-studio.tech",
    "received_at": "2026-11-17T15:04:05.123Z",
    "visitor": {
        "vid": "lq3k9x2a7f1c",
        "sid": "lq3kb0d93kfa",
        "rid": "TEST-RID-42",
        "name": "Pat Owner",
        "email": "pat.owner@gmail.com",
        "phone": "7015552368",
    },
    "geo": {"city": "Phoenix", "region": "AZ", "country": "US"},
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                  "Version/17.5 Mobile/15E148 Safari/604.1",
    "ctx": {
        "landing": "https://trace-studio.tech/demo?rid=TEST-RID-42",
        "referrer": "",
        "utm": {"utm_source": "campaign"},
        "screen": "390x844",
        "lang": "en-US",
    },
    "events": [
        {"type": "page_view", "at": "2026-11-17T15:04:01.000Z", "site": "Demo video page", "page": "/demo",
         "data": {"title": "…", "url": "…"}},
    ],
}


def ev(type_, at, page="/demo", site="Demo video page", **data):
    return {"type": type_, "at": f"2026-11-17T15:{at}.000Z", "site": site, "page": page, "data": data}


# what a prospect's visit looks like after the sample's first page view
JOURNEY = [
    ev("session_start", "04:00", landing="https://trace-studio.tech/demo?rid=X", referrer="", recipient="X"),
    ev("video_play", "04:02", video="demo", play=1, at_second=0, watched_seconds=0),
    ev("video_progress", "04:30", percent=25, watched_seconds=30),
    ev("video_progress", "05:30", percent=75, watched_seconds=90),
    ev("page_leave", "05:40", active_seconds=95, total_seconds=99, max_scroll=60),
    ev("click", "05:41", kind="demo", label="Try the live demo", href="/demos/ellison-reyes"),
    ev("page_view", "05:42", page="/demos/ellison-reyes", site="Ellison & Reyes", title="Ellison & Reyes", url="…"),
    ev("form_step", "06:00", page="/demos/ellison-reyes/start", site="Ellison & Reyes", form="bail_application",
       step=1, name="The defendant"),
    ev("form_step", "06:30", page="/demos/ellison-reyes/start", site="Ellison & Reyes", form="bail_application",
       step=2, name="About you"),
    ev("form_error", "06:40", page="/demos/ellison-reyes/start", site="Ellison & Reyes", step=2, field="Mobile"),
    ev("field_done", "06:50", page="/demos/ellison-reyes/start", site="Ellison & Reyes", form="bail_application",
       field="Mobile", filled=True, sensitive=False),
    ev("application_completed", "08:00", page="/demos/ellison-reyes/done", site="Ellison & Reyes", reference="ER-1042"),
    ev("click", "08:10", page="/demos/ellison-reyes/done", site="Ellison & Reyes", kind="call", label="Call",
       href="tel:+16025550100"),
]


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setenv("TRACKING_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    yield SECRET
    monkeypatch.delenv("TRACKING_WEBHOOK_SECRET")
    get_settings.cache_clear()


@pytest.fixture
def website(app):
    """trace-studio.tech's server: no session cookie, no CSRF token, just the bearer secret."""
    return TestClient(app)


def post(website, payload, token=SECRET):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return website.post(URL, content=json.dumps(payload), headers=headers)


def batch(rid=None, events=None, **visitor):
    p = copy.deepcopy(SAMPLE)
    p["visitor"]["rid"] = rid
    p["visitor"].update(visitor)
    if events is not None:
        p["events"] = events
    return p


def engaged_row(client, contact_id):
    rows = client.get("/api/tracking/engaged").json()["items"]
    return next((r for r in rows if r["contact_id"] == str(contact_id)), None)


# --- the email link -------------------------------------------------------------------------------

def test_campaign_demo_links_carry_the_recipient_id(client, db, outbox, compliance):  # noqa: F811
    steps = [{"subject": "Demo for {{company_name}}",
              "body": "Hi {{first_name}},\n\n[Watch the demo](https://trace-studio.tech/demo)\n\n"
                      "Or paste this: https://trace-studio.tech/demo?rid={{recipient_id}}\n\n"
                      "[Live demo](https://trace-studio.tech/demos/ellison-reyes?utm_source=email#tour)\n\n"
                      "[Our work](https://trace-studio.tech/work)\n\nBest,\nSam"}]
    mid = setup_mailbox(client, db)
    (cid,) = make_contacts(client, "lead@agency.com")
    setup_campaign(client, mid, [cid], steps=steps)
    assert sending.send_due() == {"sent": 1}
    links = db.scalars(select(TrackedLink).where(TrackedLink.contact_id == cid, TrackedLink.kind != "open_pixel")
                       .order_by(TrackedLink.ordinal)).all()
    assert [(link.kind, link.destination_url) for link in links] == [
        # https://trace-studio.tech/demo is a Demo link by default, so its clicks count as demo clicks
        ("demo", f"https://trace-studio.tech/demo?rid={cid}"),
        ("demo", f"https://trace-studio.tech/demo?rid={cid}"),  # {{recipient_id}} written by hand: not doubled
        ("demo", f"https://trace-studio.tech/demos/ellison-reyes?utm_source=email&rid={cid}#tour"),
        ("link", "https://trace-studio.tech/work"),  # not a demo link: untouched
    ]


def test_recipient_id_helpers():
    assert tracking.with_recipient("https://trace-studio.tech/demo", "abc") == "https://trace-studio.tech/demo?rid=abc"
    assert tracking.with_recipient("https://x.test/demo?a=1%202#f", "abc") == "https://x.test/demo?a=1%202&rid=abc#f"
    assert tracking.with_recipient("https://x.test/demo?rid=keep", "abc") == "https://x.test/demo?rid=keep"


# --- the endpoint ---------------------------------------------------------------------------------

def test_wrong_or_missing_secret_is_rejected_and_nothing_is_stored(client, db, website, secret):
    assert post(website, SAMPLE, token="not-the-secret").status_code == 401
    assert post(website, SAMPLE, token=None).status_code == 401
    assert website.post(URL, content=json.dumps(SAMPLE), headers={"Authorization": SECRET}).status_code == 401
    assert db.scalar(select(SiteEvent.id)) is None and db.scalar(select(SiteVisitor.id)) is None


def test_endpoint_is_off_until_the_secret_is_set(website):
    assert get_settings().tracking_webhook_secret is None
    assert post(website, SAMPLE).status_code == 503


@pytest.mark.parametrize("payload", [
    {"events": []},                                                     # no visitor
    {**SAMPLE, "visitor": {"sid": "s1"}},                               # no vid
    {**SAMPLE, "events": [SAMPLE["events"][0]] * 61},                   # more than 60 events
    {**SAMPLE, "events": "page_view"},
])
def test_batches_that_dont_match_the_spec_are_refused(website, secret, payload):
    assert post(website, payload).status_code == 422


def test_sample_payload_lands_on_the_right_prospect_and_counts_update(client, db, website, secret):
    (cid,) = make_contacts(client, "owner@desertbail.com")
    first = post(website, batch(rid=cid))
    assert first.status_code == 200, first.text
    assert first.json() | {"contact_id": None} == {"ok": True, "received": 1, "stored": 1, "duplicates": 0,
                                                    "rejected": 0, "skipped": 0, "matched": True, "contact_id": None}
    assert first.json()["contact_id"] == cid

    stored = db.scalars(select(SiteEvent)).all()
    assert [(e.type, e.page, e.site, str(e.contact_id), e.city, e.region, e.device) for e in stored] == [
        ("page_view", "/demo", "Demo video page", cid, "Phoenix", "AZ", "iPhone · Safari")]

    # the rest of the visit, then the same batch again (the website retrying): nothing is counted twice
    assert post(website, batch(rid=cid, events=JOURNEY)).json()["stored"] == len(JOURNEY)
    again = post(website, batch(rid=cid, events=JOURNEY))
    assert again.status_code == 200 and again.json()["stored"] == 0 and again.json()["duplicates"] == len(JOURNEY)
    assert db.scalar(select(SiteVisitor.event_count)) == 1 + len(JOURNEY)

    row = engaged_row(client, cid)
    assert row["level"] == "high"  # completed the application
    assert (row["demo_visits"], row["demo_completed"], row["watched_video"]) == (1, 1, 1)
    assert row["callback_requested_at"] is None

    timeline = client.get(f"/api/tracking/contacts/{cid}/timeline").json()
    (visit,) = timeline["visits"]
    assert (visit["city"], visit["region"], visit["device"]) == ("Phoenix", "AZ", "iPhone · Safari")
    assert [s["text"] for s in visit["steps"]] == [
        "Arrived from the campaign email",
        "Opened the demo video page",
        "Started the video",
        "Watched 25% of the video",
        "Watched 75% of the video",
        "Tapped “Try the live demo” to open the live demo",
        "Opened the live demo (Ellison & Reyes)",
        "Started the bail application (step 1: The defendant)",
        "Continued the bail application (step 2: About you)",
        "Entered a fake mobile, was asked for a real one",
        "Completed the application (ref ER-1042)",
        "Tapped Call",
    ]
    video_page = visit["steps"][1]
    assert (video_page["page"], video_page["seconds"], video_page["scroll"]) == ("/demo", 95, 60)

    # the CRM: one activity line per milestone, also on the combined engagement timeline
    kinds = {a.type for a in db.scalars(select(CrmActivity).where(CrmActivity.contact_id == cid))}
    assert {"site_visit", "site_demo_opened", "site_video_watched", "site_application_started",
            "site_application_completed", "site_contact_tap"} <= kinds
    assert "Completed the application (ref ER-1042)" in [i["summary"] for i in timeline["items"]]
    assert db.scalar(select(Notification.kind).where(Notification.kind == "demo_completed")) == "demo_completed"


def test_callback_request_makes_the_prospect_high_puts_them_first_and_alerts(client, db, website, secret):
    early, hot = make_contacts(client, "early@one.com", "hot@two.com")
    # "early" watched the video (Medium) more recently than "hot" did anything
    post(website, batch(rid=early, vid="v-early", sid="s-early", events=[
        ev("session_start", "10:00"), ev("video_complete", "12:00", watched_seconds=120)]))
    post(website, batch(rid=hot, vid="v-hot", sid="s-hot", events=[ev("session_start", "01:00")]))
    assert engaged_row(client, hot)["level"] == "medium"
    assert engaged_row(client, early)["level"] == "medium"
    assert client.get("/api/tracking/engaged").json()["items"][0]["contact_id"] == early

    r = post(website, batch(rid=hot, vid="v-hot", sid="s-hot",
                            events=[ev("callback_request", "02:00", agency="Desert Bail Bonds")]))
    assert r.status_code == 200
    rows = client.get("/api/tracking/engaged", params={"sort": "clicks"}).json()["items"]
    assert rows[0]["contact_id"] == hot and rows[0]["level"] == "high" and rows[0]["callback_requested_at"]
    assert client.get("/api/tracking/engaged").json()["items"][0]["contact_id"] == hot

    (n,) = db.scalars(select(Notification).where(Notification.kind == "callback_request")).all()
    assert n.title == "Call back requested: Lead1" and str(n.contact_id) == hot
    assert "7015552368" in n.body and "Desert Bail Bonds" in n.body
    # retried: still one alert
    post(website, batch(rid=hot, vid="v-hot", sid="s-hot",
                        events=[ev("callback_request", "02:00", agency="Desert Bail Bonds")]))
    assert len(db.scalars(select(Notification).where(Notification.kind == "callback_request")).all()) == 1


def test_unknown_rid_is_kept_under_the_visitor_and_linked_by_identify(client, db, website, secret):
    """The literal sample (rid TEST-RID-42) matches nobody; its identify email later matches a prospect."""
    (cid,) = make_contacts(client, "pat.owner@gmail.com")
    anonymous = batch(rid="TEST-RID-42", name=None, email=None, phone=None)
    r = post(website, anonymous)
    assert r.json()["matched"] is False
    assert db.scalar(select(SiteEvent.contact_id)) is None
    assert db.scalar(select(SiteVisitor.vid)) == "lq3k9x2a7f1c"

    r = post(website, batch(rid="TEST-RID-42", events=[
        ev("identify", "09:00", name="Pat Owner", email="Pat.Owner@gmail.com", phone="(701) 555-2368")]))
    assert r.json()["matched"] is True and r.json()["contact_id"] == cid
    # the earlier page view moved to the prospect too
    assert {str(e.contact_id) for e in db.scalars(select(SiteEvent))} == {cid}
    c = db.get(Contact, cid)
    db.refresh(c)
    assert (c.first_name, c.last_name, c.phone) == ("Pat", "Owner", "(701) 555-2368")
    assert engaged_row(client, cid)["level"] == "high"


def test_identify_by_phone_and_a_different_email_is_kept_beside_the_prospects(client, db, website, secret):
    (cid,) = make_contacts(client, "office@desertbail.com")
    db.execute(update(Contact).where(Contact.id == cid).values(phone="+1 701-555-2368"))
    db.commit()
    r = post(website, batch(rid=None, vid="v2", events=[
        ev("identify", "09:00", name="Pat Owner", email="pat.owner@gmail.com", phone="7015552368")]))
    assert r.json()["contact_id"] == cid
    c = db.get(Contact, cid)
    db.refresh(c)
    # campaigns keep sending to the address they were sending to
    assert c.email == "office@desertbail.com" and c.custom_fields["website_email"] == "pat.owner@gmail.com"
    assert (c.first_name, c.last_name) == ("Pat", "Owner")
    (a,) = db.scalars(select(CrmActivity).where(CrmActivity.type == "site_identify")).all()
    assert "pat.owner@gmail.com" in a.summary


def test_card_bank_pin_and_password_content_is_never_stored(client, db, website, secret):
    (cid,) = make_contacts(client, "lead@agency.com")
    events = [
        ev("field_done", "01:00", form="payment", field="Card number", filled=True, sensitive=True),  # label only: fine
        {**ev("form_submit", "02:00", form="payment"), "data": {"form": "payment", "card_number": "4111111111111111"}},
        {**ev("form_submit", "03:00", form="payment"), "data": {"form": "payment", "note": "4242 4242 4242 4242"}},
        {**ev("form_submit", "04:00", form="login"), "data": {"form": "login", "password": "hunter2"}},
        {**ev("form_submit", "05:00", form="checkin"), "data": {"form": "checkin", "pin": "1234"}},
        {**ev("form_submit", "06:00", form="payment"), "data": {"form": "payment", "routing_number": "021000021"}},
        {**ev("click", "07:00"), "data": {"kind": "button", "label": "Pay", "extra": "kept out"}},
    ]
    r = post(website, batch(rid=cid, events=events)).json()
    assert (r["stored"], r["rejected"]) == (2, 5)
    stored = db.scalars(select(SiteEvent).order_by(SiteEvent.occurred_at)).all()
    assert [(e.type, e.data) for e in stored] == [
        ("field_done", {"form": "payment", "field": "Card number", "filled": True, "sensitive": True}),
        ("click", {"kind": "button", "label": "Pay"}),  # keys outside the spec are dropped
    ]
    everything = json.dumps([e.data for e in db.scalars(select(SiteEvent))])
    assert not re.search(r"4111|4242|hunter2|1234|021000021", everything)


def test_unknown_event_types_are_skipped_not_fatal(client, db, website, secret):
    (cid,) = make_contacts(client, "lead@agency.com")
    r = post(website, batch(rid=cid, events=[ev("mystery", "01:00"), "not-an-event", ev("tab_hidden", "02:00")])).json()
    assert (r["stored"], r["skipped"]) == (1, 2)


def test_old_website_events_are_purged_with_the_other_raw_events(client, db, website, secret):
    (cid,) = make_contacts(client, "lead@agency.com")
    post(website, batch(rid=cid))
    db.execute(update(SiteEvent).values(occurred_at=utcnow() - timedelta(days=400)))
    db.commit()
    assert tracking.purge(db)["site_events"] == 1


def test_settings_show_the_endpoint_and_the_secret_to_admins_only(app, client, db, secret):
    from tests.conftest import login, make_user

    s = client.get("/api/tracking/site").json()
    assert s["endpoint"].endswith("/api/track/site-events") and s["configured"] and s["secret"] == SECRET
    make_user("viewer@trace.studio", role="viewer")
    member = login(app, "viewer@trace.studio")
    r = member.get("/api/tracking/site")
    assert r.status_code == 200 and r.json()["secret"] is None


def test_device_names():
    assert site_tracking.device(SAMPLE["user_agent"]) == "iPhone · Safari"
    assert site_tracking.device("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/131.0 Safari/537.36") == "Windows · Chrome"
    assert site_tracking.device(None) is None


def test_sent_message_campaign_is_attributed(client, db, website, secret, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    (cid,) = make_contacts(client, "lead@agency.com")
    camp = setup_campaign(client, mid, [cid], steps=[{"subject": "Hi", "body": "Hi {{first_name}}\n\nBest,\nSam"}])
    assert sending.send_due() == {"sent": 1}
    assert db.scalar(select(SentMessage.campaign_id)) is not None
    post(website, batch(rid=cid, events=[ev("session_start", "04:00"), *SAMPLE["events"]]))
    assert {str(c) for c in db.scalars(select(SiteEvent.campaign_id))} == {camp}
    rows = client.get("/api/tracking/engaged", params={"campaign_id": camp}).json()["items"]
    assert [r["contact_id"] for r in rows] == [cid]
