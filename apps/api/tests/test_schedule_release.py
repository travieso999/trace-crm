"""Changing a sending window takes effect on messages already waiting for the old one. Sandbox provider: nothing is sent."""

from sqlalchemy import select

from app.models import CampaignContact, ScheduledMessage
from app.models.base import utcnow
from app.services import sending
from tests.test_sending import ALWAYS, make_contacts, outbox, sent_files, setup_campaign, setup_mailbox  # noqa: F401


def closed_today() -> dict:
    """A window open every day except today, like a Mon-Fri schedule on a Saturday."""
    return {**ALWAYS, "days": [d for d in range(7) if d != utcnow().weekday()]}


def waiting(db) -> ScheduledMessage:
    db.expire_all()
    return db.scalar(select(ScheduledMessage))


def test_opening_the_mailbox_schedule_releases_messages_already_waiting(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db, schedule=closed_today())
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"))
    assert sending.send_due() == {"deferred": 1}
    sm = waiting(db)
    assert sm.status == "queued" and sm.due_at > utcnow()
    assert sm.last_error == "outside sending window (mailbox schedule)"

    assert client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS}).status_code == 200
    sm = waiting(db)
    assert sm.due_at <= utcnow()
    assert db.scalar(select(CampaignContact.next_send_at)) <= utcnow()
    assert sending.send_due() == {"sent": 1}
    assert len(sent_files(outbox)) == 1


def test_opening_the_campaign_schedule_releases_messages_already_waiting(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"),
                         schedule={**closed_today(), "use_recipient_timezone": False})
    assert sending.send_due() == {"deferred": 1}
    assert waiting(db).last_error == "outside sending window (campaign schedule)"

    r = client.patch(f"/api/campaigns/{cid}", json={"schedule": {**ALWAYS, "use_recipient_timezone": False}})
    assert r.status_code == 200
    assert sending.send_due() == {"sent": 1}


def test_a_change_that_leaves_the_window_closed_still_sends_nothing(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db, schedule=closed_today())
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"))
    assert sending.send_due() == {"deferred": 1}
    assert client.patch(f"/api/email-accounts/{mid}", json={"schedule": {**closed_today(), "end": "20:00"}}).status_code == 200
    assert sending.send_due() == {"deferred": 1}
    assert not (outbox / "outreach@trace.studio" / "sent").exists()
    assert waiting(db).due_at > utcnow()


def test_both_windows_must_be_open(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db, schedule=closed_today())
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"),
                         schedule={**closed_today(), "use_recipient_timezone": False})
    assert sending.send_due() == {"deferred": 1}
    client.patch(f"/api/campaigns/{cid}", json={"schedule": {**ALWAYS, "use_recipient_timezone": False}})
    assert sending.send_due() == {"deferred": 1}  # the mailbox is still closed
    assert waiting(db).last_error == "outside sending window (mailbox schedule)"
    client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS})
    assert sending.send_due() == {"sent": 1}


def test_a_schedule_change_leaves_other_waits_alone(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    setup_campaign(client, mid, make_contacts(client, "a@acme.com"))
    assert sending.send_due() == {"sent": 1}
    assert sending.send_due() == {}
    # the follow-up keeps its own delay: it isn't waiting on a window
    before = db.scalar(select(CampaignContact.next_send_at))
    assert before > utcnow()
    client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS})
    db.expire_all()
    assert db.scalar(select(CampaignContact.next_send_at)) == before


def test_send_now_reaches_a_message_already_pushed_to_a_later_time(client, db, outbox, compliance):  # noqa: F811
    mid = setup_mailbox(client, db)
    cid = setup_campaign(client, mid, make_contacts(client, "a@acme.com"),
                         schedule={**closed_today(), "use_recipient_timezone": False})
    assert sending.send_due() == {"deferred": 1}
    step = client.get(f"/api/campaigns/{cid}").json()["steps"][0]["id"]
    # the window was fixed directly, so only send-now can bring the message forward
    db.execute(sending.Campaign.__table__.update().values(schedule={**ALWAYS, "use_recipient_timezone": False}))
    db.commit()
    assert sending.send_due() == {}
    assert client.post(f"/api/campaigns/{cid}/steps/{step}/send-now").status_code == 200
    assert sending.send_due() == {"sent": 1}
