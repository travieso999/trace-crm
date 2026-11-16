"""A bell notice when an address is added to the suppression list, whichever way it got there."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import CrmOpportunity, Notification
from app.services import inbound, sending
from tests.conftest import login, make_user


def _notices(db, user_id=None):
    db.expire_all()
    stmt = select(Notification).where(Notification.kind == "suppression")
    if user_id:
        stmt = stmt.where(Notification.user_id == user_id)
    return db.scalars(stmt.order_by(Notification.created_at)).all()


def _prospect(client, email="ana@alpha-bail.com"):
    r = client.post("/api/contacts", json={"email": email, "first_name": "Ana", "last_name": "Diaz", "company_name": "Alpha Bail"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_unsubscribe_link_notifies_the_admins(app, client, db):
    cid = _prospect(client)
    token = sending.unsubscribe_url(uuid.UUID(cid), None).rsplit("/u/", 1)[1]
    assert TestClient(app).post(f"/u/{token}").status_code == 200
    [n] = _notices(db)
    assert n.title == "Added to the suppression list: Ana Diaz (ana@alpha-bail.com)"
    assert n.body == "Unsubscribed · Alpha Bail · unsubscribe link" and str(n.contact_id) == cid
    # clicking it again doesn't notify twice
    TestClient(app).post(f"/u/{token}")
    assert len(_notices(db)) == 1
    item = client.get("/api/notifications").json()["items"][0]
    assert item["kind"] == "suppression" and item["contact_id"] == cid


def test_a_bounce_or_removal_reply_notifies_once(client, db):
    _prospect(client)
    inbound.suppress(db, "ana@alpha-bail.com", "bounce", "bounce via sam@trace-studio.tech")
    inbound.suppress(db, "ana@alpha-bail.com", "bounce", "bounce via sam@trace-studio.tech")
    db.commit()
    [n] = _notices(db)
    assert n.body.startswith("Bounced · Alpha Bail")


def test_manual_adds_notify_the_other_admins_but_not_whoever_added_them(app, client, db):
    other = make_user(email="second@trace.studio", role="admin")
    r = client.post("/api/suppressions", json={"emails": ["one@x.com"], "reason": "manual"})
    assert r.json()["added"] == 1
    notices = _notices(db)
    assert [n.user_id for n in notices] == [other]
    assert notices[0].title == "Added to the suppression list: one@x.com" and "by Test User" in notices[0].body

    client.post("/api/suppressions", json={"emails": ["a@x.com", "b@x.com", "c@x.com", "one@x.com"], "reason": "manual"})
    bulk = _notices(db, other)[-1]
    assert bulk.title == "3 addresses added to the suppression list"  # one summary, existing address not counted
    assert "a@x.com, b@x.com, c@x.com" in bulk.body


def test_the_prospects_owner_is_told_too(app, client, db):
    member = make_user(email="rep@trace.studio", role="member")
    cid = _prospect(client)
    db.add(CrmOpportunity(contact_id=uuid.UUID(cid), owner_id=member))
    db.commit()
    inbound.suppress(db, "ana@alpha-bail.com", "reply_request", "unsubscribe reply")
    db.commit()
    assert member in {n.user_id for n in _notices(db)}
    rep = login(app, email="rep@trace.studio")
    assert rep.get("/api/notifications").json()["items"][0]["kind"] == "suppression"
