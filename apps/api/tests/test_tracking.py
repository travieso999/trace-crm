"""Engagement tracking: per-recipient links, click redirects, scanner flags, demo attribution, dashboards, privacy."""

import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.models import (
    CrmActivity,
    DemoEvent,
    DemoSession,
    ScheduledMessage,
    SentMessage,
    SuppressionEntry,
    TrackedLink,
    TrackingEvent,
)
from app.models.base import utcnow
from app.services import app_settings, sending, tracking
from tests.test_sending import make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401

DEMO = "https://trace-studio.tech/demos/ellison-reyes/index.html?utm_source=email&v=2#tour"
SITE = "https://trace-studio.tech/work"
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
ORIGIN = {"Origin": "https://trace-studio.tech"}
STEPS = [{"subject": "Bail software for {{company_name}}",
          "body": f"Hi {{{{first_name}}}},\n\n[Watch 360° Demo]({DEMO})\n\nMore: [our work]({SITE})\n\nBest,\nSam"}]


@pytest.fixture
def visitor(app):
    """A browser with no CRM session: what a prospect clicking in their mail app looks like."""
    return TestClient(app, headers={"User-Agent": BROWSER})


def _send(client, db, emails):
    mid = setup_mailbox(client, db)
    ids = make_contacts(client, *emails)
    cid = setup_campaign(client, mid, ids, steps=STEPS)
    assert sending.send_due() == {"sent": len(emails)}
    # a person reads the email minutes later; a click within seconds of sending is the scanner rule's business
    db.execute(update(SentMessage).values(sent_at=utcnow() - timedelta(minutes=10)))
    db.commit()
    return cid, dict(zip(emails, ids, strict=True))


def with_rid(url: str, contact_id) -> str:
    return url.replace("#tour", f"&rid={contact_id}#tour")


def _links(db, contact_id, kind=None):
    stmt = select(TrackedLink).where(TrackedLink.contact_id == contact_id, TrackedLink.kind != "open_pixel")
    if kind:
        stmt = stmt.where(TrackedLink.kind == kind)
    return db.scalars(stmt.order_by(TrackedLink.ordinal)).all()


def _click(visitor, token, **headers):
    return visitor.get(f"/t/{token}", follow_redirects=False, headers=headers or None)


def _demo(visitor, session_token, event, **extra):
    body = json.dumps({"t": session_token, "e": event, **extra})
    return visitor.post("/api/track/demo", content=body, headers={**ORIGIN, "Content-Type": "text/plain"})


def _session_token(response) -> str:
    return parse_qs(urlsplit(response.headers["location"]).query)["trc"][0]


# --- rewriting ------------------------------------------------------------------------------------

def test_three_recipients_get_their_own_links_and_unsubscribe_is_untouched(client, db, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com", "b@two.com", "c@three.com"])
    tokens = set()
    for contact_id in ids.values():
        links = _links(db, contact_id)
        assert [(link.kind, link.destination_url, link.label) for link in links] == [
            # demo links carry the prospect's id for trace-studio.tech's own tracking; other links are untouched
            ("demo", with_rid(DEMO, contact_id), "Watch 360° Demo"), ("link", SITE, "our work")]
        tokens |= {link.token for link in links}
        assert all(link.sent_message_id for link in links)
    assert len(tokens) == 6  # never shared between recipients or links

    for msg in sent_files(outbox):
        html = msg.get_body(("html",)).get_content()
        text = msg.get_body(("plain",)).get_content()
        assert DEMO.split("?")[0] not in html and "/t/" in html  # rewritten
        # a tracking URL is the base, /t/ and a random token: nothing about the recipient
        hrefs = re.findall(r'href="([^"]*/t/[^"]*)"', html)
        assert hrefs and all(re.fullmatch(r"http://localhost:3000/t/[A-Za-z0-9_-]{24}", h) for h in hrefs)
        assert msg["To"] not in " ".join(hrefs)
        unsub = re.search(r"https?://[^\s<>\"]+/u/[^\s<>\")]+", text).group(0)
        assert unsub in html  # unsubscribe link exactly as before, not wrapped
        assert msg["List-Unsubscribe"] == f"<{unsub}>"
        assert "/t/" in text and DEMO not in text  # plain-text part points at the same tracked links


def test_sensitive_links_are_never_rewritten():
    for url in ("https://x.com/unsubscribe?id=1", "https://app.x.com/login", "https://x.com/reset-password",
                "https://x.com/verify?token=abc", "mailto:sam@x.com", "tel:+15551234567", "https://x.com/oauth/start"):
        assert not tracking.should_track(url), url
    assert tracking.should_track("https://trace-studio.tech/demos/ellison-reyes?utm_source=email")


def test_a_retry_reuses_the_tokens_already_rendered(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    [cid_] = make_contacts(client, "retry@x.com")
    setup_campaign(client, mid, [cid_], steps=STEPS, start=True)
    with sending.SessionLocal() as s:
        sending.dispatch_due(s)
    sm = db.scalar(select(ScheduledMessage))
    r = sending.Rendered(subject="S", text=f"see {DEMO}", html=f'<div><a href="{DEMO.replace("&", "&amp;")}">Demo</a></div>')
    first = tracking.apply_to_email(db, r, sm, cid_)
    second = tracking.apply_to_email(db, r, sm, cid_)
    assert first.html == second.html and first.text == second.text
    assert db.scalar(select(func.count()).select_from(TrackedLink)) == 1


def test_test_sends_are_never_tracked(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "x@x.com"), steps=STEPS, start=False)
    r = client.post("/api/campaigns/test-send", json={**STEPS[0], "campaign_id": cid, "email_account_id": mid,
                                                      "to_email": "admin@trace.studio"})
    assert r.status_code == 200, r.text
    assert db.scalar(select(func.count()).select_from(TrackedLink)) == 0
    assert "ellison-reyes/index.html" in sent_files(outbox)[0].get_body(("html",)).get_content()


# --- clicks ---------------------------------------------------------------------------------------

def test_clicks_and_demo_events_are_attributed_to_each_recipient_independently(client, db, visitor, outbox, compliance):  # noqa: F811
    cid, ids = _send(client, db, ["a@one.com", "b@two.com", "c@three.com"])
    a, b, c = ids["a@one.com"], ids["b@two.com"], ids["c@three.com"]

    # A clicks the demo link in a browser: lands on the demo with every original parameter kept
    r = _click(visitor, _links(db, a, "demo")[0].token)
    assert r.status_code == 302
    loc = urlsplit(r.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path, loc.fragment) == ("https", "trace-studio.tech", "/demos/ellison-reyes/index.html", "tour")
    q = parse_qs(loc.query)
    assert q["utm_source"] == ["email"] and q["v"] == ["2"] and len(q["trc"][0]) >= 24
    assert "a@one.com" not in r.headers["location"]
    assert r.headers["referrer-policy"] == "no-referrer"
    a_session = q["trc"][0]

    # B's mail gateway follows the link first (flagged), then B fills in the demo from that session
    r = _click(visitor, _links(db, b, "demo")[0].token, **{"User-Agent": "Mozilla/5.0 (compatible; Barracuda Sentinel)"})
    b_session = _session_token(r)
    # C clicks only the ordinary link
    assert _click(visitor, _links(db, c, "link")[0].token).headers["location"] == SITE

    for event in ({"e": "page_view", "page": "home"}, {"e": "application_started", "page": "start", "form": "application"},
                  {"e": "signature_completed", "page": "start"},
                  {"e": "application_submitted", "page": "start", "signed": True, "sample": False,
                   "email": "leak@x.com", "fields": {"Defendant": "John"}}):  # extra fields are ignored
        assert _demo(visitor, a_session, event.pop("e"), **event).status_code == 204
    assert _demo(visitor, b_session, "application_started", page="start").status_code == 204

    db.expire_all()
    by_contact = {str(e.contact_id): e for e in db.scalars(select(DemoEvent).where(DemoEvent.type == "application_submitted"))}
    assert set(by_contact) == {a} and by_contact[a].signed is True
    assert db.scalar(select(func.count()).select_from(DemoEvent).where(DemoEvent.contact_id == b)) == 1
    assert db.scalar(select(func.count()).select_from(DemoEvent).where(DemoEvent.contact_id == c)) == 0
    b_click = db.scalar(select(TrackingEvent).where(TrackingEvent.contact_id == b))
    assert b_click.suspected_bot and "scanner_user_agent" in b_click.bot_reasons and b_click.verified_human

    rows = {r["email"]: r for r in client.get("/api/tracking/emails").json()["items"]}
    assert (rows["a@one.com"]["clicks"], rows["a@one.com"]["demo_clicks"], rows["a@one.com"]["demo_visits"],
            rows["a@one.com"]["demo_completed"]) == (1, 1, 1, 1)
    assert (rows["b@two.com"]["clicks"], rows["b@two.com"]["scanner_clicks"], rows["b@two.com"]["demo_completed"]) == (1, 0, 0)
    assert (rows["c@three.com"]["clicks"], rows["c@three.com"]["demo_clicks"], rows["c@three.com"]["demo_visits"]) == (1, 0, 0)
    assert rows["a@one.com"]["links"][0]["label"] == "Watch 360° Demo" and rows["a@one.com"]["campaign"] == "TX bail bonds"
    assert client.get("/api/tracking/emails", params={"demo": "completed"}).json()["total"] == 1
    assert client.get("/api/tracking/emails", params={"clicked": "yes", "q": "three"}).json()["total"] == 1

    engaged = {r["email"]: r for r in client.get("/api/tracking/engaged").json()["items"]}
    assert engaged["a@one.com"]["level"] == "high" and engaged["b@two.com"]["level"] == "high"
    assert engaged["c@three.com"]["level"] == "medium"
    assert client.get("/api/tracking/engaged", params={"sort": "demo_completed"}).json()["items"][0]["email"] == "a@one.com"

    summaries = [i["summary"] for i in client.get(f"/api/tracking/contacts/{a}/timeline").json()["items"]]
    assert "Clicked “Watch 360° Demo”" in summaries
    assert "Completed Bail Application Demo — Signed Electronically" in summaries
    assert "Visited the demo" in summaries and "Started Bail Application Demo" in summaries
    c_summaries = [i["summary"] for i in client.get(f"/api/tracking/contacts/{c}/timeline").json()["items"]]
    assert not any("Demo" in s for s in c_summaries)
    # one deduplicated activity per step; the general Activity tab gets them too
    acts = db.scalars(select(CrmActivity.summary).where(CrmActivity.contact_id == a, CrmActivity.type.like("demo%"))).all()
    assert sorted(acts) == ["Completed Bail Application Demo — Signed Electronically", "Started Bail Application Demo",
                            "Visited the demo"]


def test_repeat_clicks_count_but_the_timeline_shows_one_entry(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    token = _links(db, ids["a@one.com"], "link")[0].token
    for _ in range(3):
        _click(visitor, token)
    row = client.get("/api/tracking/emails").json()["items"][0]
    assert (row["clicks"], row["unique_clicks"]) == (3, 1)
    items = client.get(f"/api/tracking/contacts/{ids['a@one.com']}/timeline").json()["items"]
    assert [i["count"] for i in items if i["kind"] == "email_click"] == [3]
    raw = client.get(f"/api/tracking/contacts/{ids['a@one.com']}/timeline", params={"raw": True}).json()["raw"]
    assert len([e for e in raw if e["type"] == "click"]) == 3


def test_unknown_tokens_and_tampered_urls_never_redirect_elsewhere(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    for bad in ("nope", "A" * 24, "../../etc"):
        r = visitor.get(f"/t/{bad}", follow_redirects=False)
        assert r.status_code == 404 and "location" not in r.headers
    token = _links(db, ids["a@one.com"], "link")[0].token
    r = visitor.get(f"/t/{token}?url=https://evil.example.com&next=//evil.example.com", follow_redirects=False)
    assert r.headers["location"] == SITE


def test_scanner_rules(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    demo, site = (link.token for link in _links(db, ids["a@one.com"]))
    # a gateway opening every link of the email at once
    _click(visitor, demo)
    _click(visitor, site)
    db.expire_all()
    assert all(e.suspected_bot and "multiple_links_at_once" in e.bot_reasons for e in db.scalars(select(TrackingEvent)))
    # HEAD, no user agent, and a click seconds after delivery
    db.execute(update(SentMessage).values(sent_at=utcnow()))
    db.commit()
    assert visitor.head(f"/t/{site}", follow_redirects=False).status_code == 302
    TestClient(visitor.app, headers={"User-Agent": ""}).get(f"/t/{site}", follow_redirects=False)
    db.expire_all()
    reasons = [set(e.bot_reasons) for e in db.scalars(select(TrackingEvent).order_by(TrackingEvent.occurred_at))][2:]
    assert {"head_request", "seconds_after_delivery"} <= reasons[0]
    assert "no_user_agent" in reasons[1]
    row = client.get("/api/tracking/emails").json()["items"][0]
    assert row["clicks"] == 0 and row["scanner_clicks"] == 4
    assert client.get("/api/tracking/engaged").json()["total"] == 0  # scanners are not engagement


def test_a_teammate_previewing_in_the_crm_is_not_counted(client, db, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    r = client.get(f"/t/{_links(db, ids['a@one.com'], 'demo')[0].token}", follow_redirects=False)
    assert r.status_code == 302 and "trc=" not in r.headers["location"]
    e = db.scalar(select(TrackingEvent))
    assert e.suspected_bot and e.bot_reasons == ["crm_user"]
    assert db.scalar(select(func.count()).select_from(DemoSession)) == 0


def test_opted_out_prospects_still_reach_the_page_but_nothing_is_recorded(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    db.add(SuppressionEntry(email="a@one.com", reason="unsubscribe"))
    db.commit()
    r = _click(visitor, _links(db, ids["a@one.com"], "demo")[0].token)
    assert r.status_code == 302 and r.headers["location"] == with_rid(DEMO, ids["a@one.com"])
    assert db.scalar(select(func.count()).select_from(TrackingEvent)) == 0


# --- demo endpoint --------------------------------------------------------------------------------

def test_demo_endpoint_only_accepts_known_sessions_from_allowed_origins(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    session = _session_token(_click(visitor, _links(db, ids["a@one.com"], "demo")[0].token))
    evil = visitor.post("/api/track/demo", content=json.dumps({"t": session, "e": "application_submitted"}),
                        headers={"Origin": "https://evil.example.com", "Content-Type": "text/plain"})
    assert evil.status_code == 204 and "access-control-allow-origin" not in evil.headers
    assert _demo(visitor, "x" * 32, "page_view").status_code == 204  # unknown session: silently ignored
    assert _demo(visitor, session, "drop_tables").status_code == 204
    pre = visitor.options("/api/track/demo", headers={**ORIGIN, "Access-Control-Request-Method": "POST"})
    assert pre.headers["access-control-allow-origin"] == "https://trace-studio.tech"
    assert db.scalar(select(func.count()).select_from(DemoEvent)) == 0
    # an expired session no longer counts
    db.execute(update(DemoSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    db.commit()
    _demo(visitor, session, "page_view")
    assert db.scalar(select(func.count()).select_from(DemoEvent)) == 0


def test_demo_completion_notifies_the_admins(client, db, visitor, outbox, compliance):  # noqa: F811
    from app.models import Notification

    _, ids = _send(client, db, ["a@one.com"])
    session = _session_token(_click(visitor, _links(db, ids["a@one.com"], "demo")[0].token))
    _demo(visitor, session, "application_submitted", signed=True)
    _demo(visitor, session, "application_submitted", signed=True)  # twice: still one notice, one activity
    n = db.scalars(select(Notification).where(Notification.kind == "demo_completed")).all()
    assert len(n) == 1 and "Completed Bail Application Demo — Signed Electronically" in n[0].title


# --- opens, settings, retention -------------------------------------------------------------------

def test_open_pixel_only_when_turned_on_and_opens_are_estimates(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["off@x.com"])
    assert "/t/o/" not in sent_files(outbox)[0].get_body(("html",)).get_content()
    assert client.get("/api/tracking/emails").json()["items"][0]["opens"] is None  # not measured, not zero
    assert client.put("/api/settings/tracking", json={"open_tracking": True}).status_code == 200
    mid =db.scalar(select(SentMessage.email_account_id))
    [cid] = make_contacts(client, "on@x.com")
    camp = setup_campaign(client, str(mid), [cid], steps=STEPS)
    assert camp and sending.send_due() == {"sent": 1}
    db.execute(update(SentMessage).values(sent_at=utcnow() - timedelta(minutes=10)))
    db.commit()
    html = [m for m in sent_files(outbox) if m["To"] == "on@x.com"][0].get_body(("html",)).get_content()
    token = re.search(r"/t/o/([A-Za-z0-9_-]+)\.gif", html).group(1)
    r = visitor.get(f"/t/o/{token}.gif")
    assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
    visitor.get(f"/t/o/{token}.gif", headers={"User-Agent": "Mozilla/5.0 (via ggpht.com GoogleImageProxy)"})
    row = next(r for r in client.get("/api/tracking/emails").json()["items"] if r["email"] == "on@x.com")
    assert row["opens"] == 2
    level = next(r for r in client.get("/api/tracking/engaged").json()["items"] if r["email"] == "on@x.com")["level"]
    assert level == "low"


@pytest.mark.parametrize("body", [{"open_tracking": "yes"}, {"demo_urls": ["http://insecure.example.com"]},
                                  {"retention_days": 5}, {"unknown": 1}])
def test_tracking_settings_are_validated(client, body):
    assert client.put("/api/settings/tracking", json=body).status_code == 422


def test_click_tracking_can_be_turned_off(client, db, outbox, compliance):  # noqa: F811
    app_settings.update_section(db, "tracking", {"click_tracking": False})
    db.commit()
    _send(client, db, ["a@one.com"])
    html = sent_files(outbox)[0].get_body(("html",)).get_content()
    assert "/t/" not in html and "ellison-reyes/index.html" in html


def test_a_tracking_failure_never_stops_an_email(client, db, outbox, compliance, monkeypatch):  # noqa: F811
    def broken(*a, **kw):
        db_ = a[0]
        db_.execute(select(func.count()).select_from(TrackedLink))  # real work in the savepoint first
        raise RuntimeError("tracking table missing")

    monkeypatch.setattr(tracking, "_apply", broken)
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@one.com"), steps=STEPS)
    assert sending.send_due() == {"sent": 1}
    html = sent_files(outbox)[0].get_body(("html",)).get_content()
    assert "ellison-reyes/index.html" in html and "/t/" not in html  # sent exactly as rendered, untracked


def test_old_raw_events_are_purged_but_the_timeline_entry_stays(client, db, visitor, outbox, compliance):  # noqa: F811
    _, ids = _send(client, db, ["a@one.com"])
    _click(visitor, _links(db, ids["a@one.com"], "link")[0].token)
    db.execute(update(TrackingEvent).values(occurred_at=utcnow() - timedelta(days=400)))
    db.commit()
    assert tracking.purge(db) == {"tracking_events": 1}
    db.commit()
    assert db.scalar(select(func.count()).select_from(CrmActivity).where(CrmActivity.type == "email_click")) == 1


def test_dashboards_need_the_right_permissions(app, client, db):
    from tests.conftest import login, make_user

    make_user(email="member@trace.studio", role="member")
    member = login(app, email="member@trace.studio")
    assert member.get("/api/tracking/emails").status_code == 403  # Outreach is off for members by default
    assert member.get("/api/tracking/engaged").status_code == 200
    assert TestClient(app).get("/api/tracking/emails").status_code == 401


def test_attribution_check_says_plainly_when_nothing_links_a_visit(client, db, outbox, compliance):  # noqa: F811
    from datetime import datetime

    from app.services import attribution_check

    # an email sent with the demo link before tracking existed (no tracked links)
    app_settings.update_section(db, "tracking", {"click_tracking": False})
    db.commit()
    _send(client, db, ["old@x.com"])
    lines = attribution_check.run(db, datetime.fromisoformat("2026-11-16T10:02:00-05:00"))
    text = "\n".join(lines)
    assert "Campaign emails containing the demo link: 1" in text
    assert "Tracked links ever created: 0" in text
    assert "cannot be attributed" in text and "not used to identify anyone" in text
