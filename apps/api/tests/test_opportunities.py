"""Opportunities CRM: stages, board, details, tasks + reminders, notes + revisions, activity, notifications."""

from datetime import timedelta

from sqlalchemy import select

from app.core.config import get_settings
from app.models import Contact, CrmOpportunity, CrmTask, Notification, PipelineStage, User
from app.models.base import utcnow
from app.services import contacts as contact_service
from app.services import reminders
from tests.conftest import login, make_user


def lead(client, email="owner@acmebail.com", **kw):
    """An opportunity created on the board (the person is created too)."""
    r = client.post("/api/opportunities", json={"email": email, "first_name": "Ann", "last_name": "Lee",
                                                "company_name": "Acme Bail", "phone": "512-555-0100", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def prospect(client, email, **kw):
    """A campaign prospect (imported/added in Prospects): not an opportunity."""
    r = client.post("/api/contacts", json={"email": email, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def stage_named(client, name):
    return next(s for s in client.get("/api/opportunities/stages").json() if s["name"] == name)


def column_of(client, contact_id):
    for s in client.get("/api/opportunities").json()["stages"]:
        if any(i["id"] == contact_id for i in s["items"]):
            return s["name"]
    return None


# --- stages and board -----------------------------------------------------------------------------

def test_seeded_stages_mirror_lead_statuses_and_board_cards(client):
    stages = client.get("/api/opportunities/stages").json()
    assert [s["name"] for s in stages][:3] == ["New lead", "Contacted", "Replied"]
    assert all(s["lead_status"] for s in stages)
    cid = lead(client)
    board = client.get("/api/opportunities").json()["stages"]
    card = board[0]["items"][0]
    assert board[0]["name"] == "New lead" and card["id"] == cid
    assert card["first_name"] == "Ann" and card["company_name"] == "Acme Bail" and card["email"] == "owner@acmebail.com"
    assert card["phone"] == "512-555-0100" and card["stage_name"] == "New lead" and card["next_task"] is None


def test_stage_create_rename_color_reorder_and_validation(client, app):
    new = client.post("/api/opportunities/stages", json={"name": "Negotiation", "color": "#123ABC"})
    assert new.status_code == 201
    neg = next(s for s in new.json() if s["name"] == "Negotiation")
    assert neg["color"] == "#123abc" and neg["lead_status"] is None and neg["position"] == len(new.json()) - 1
    r = client.patch(f"/api/opportunities/stages/{neg['id']}", json={"name": "Negotiating", "color": "#00ff00"})
    assert any(s["name"] == "Negotiating" and s["color"] == "#00ff00" for s in r.json())
    assert client.patch(f"/api/opportunities/stages/{neg['id']}", json={"color": "red"}).status_code == 422
    # a lead status can be linked to only one stage
    assert client.patch(f"/api/opportunities/stages/{neg['id']}", json={"lead_status": "won"}).status_code == 422

    ids = [s["id"] for s in client.get("/api/opportunities/stages").json()]
    order = [ids[-1], *ids[:-1]]
    assert [s["id"] for s in client.put("/api/opportunities/stages/order", json={"ids": order}).json()] == order
    assert client.put("/api/opportunities/stages/order", json={"ids": order[1:]}).status_code == 422

    make_user("member@trace.studio", role="member")
    m = login(app, "member@trace.studio")
    assert m.post("/api/opportunities/stages", json={"name": "X"}).status_code == 403  # admins manage stages
    assert m.get("/api/opportunities/stages").status_code == 200


def test_move_sets_linked_lead_status_and_automation_respects_custom_stages(client, db):
    cid = lead(client)
    won = stage_named(client, "Won")
    assert client.post(f"/api/opportunities/{cid}/move", json={"stage_id": won["id"]}).json()["status"] == "won"
    db.expire_all()
    assert db.get(Contact, cid).lead_status == "won"
    assert db.get(Contact, cid).company_id is not None  # a won deal makes the business a client company

    custom = next(s for s in client.post("/api/opportunities/stages", json={"name": "Negotiation"}).json()
                  if s["name"] == "Negotiation")
    r = client.post(f"/api/opportunities/{cid}/move", json={"stage_id": custom["id"]})
    assert r.json()["stage_name"] == "Negotiation" and r.json()["status"] == "open"  # reopened out of Won
    db.expire_all()
    assert db.get(Contact, cid).lead_status == "won"  # an unlinked stage leaves the lead status alone

    # automation (e.g. a reply) does not pull a card out of a stage the user chose...
    c = db.get(Contact, cid)
    contact_service.set_lead_status(db, c, "replied", None, reason="reply")
    db.commit()
    assert column_of(client, cid) == "Negotiation"
    # ...but a card in a linked stage follows its lead status
    client.post(f"/api/opportunities/{cid}/move", json={"stage_id": stage_named(client, "Interested")["id"]})
    db.expire_all()
    contact_service.set_lead_status(db, db.get(Contact, cid), "meeting_scheduled", None, reason="meeting")
    db.commit()
    assert column_of(client, cid) == "Meeting scheduled"


def test_delete_stage_moves_opportunities_and_keeps_records(client, db):
    a, b = lead(client, "a@acmebail.com"), lead(client, "b@acmebail.com")
    replied = stage_named(client, "Replied")
    for cid in (a, b):
        client.post(f"/api/opportunities/{cid}/move", json={"stage_id": replied["id"]})
    custom = next(s for s in client.post("/api/opportunities/stages", json={"name": "Hot"}).json() if s["name"] == "Hot")
    assert stage_named(client, "Replied")["count"] == 2

    assert client.delete(f"/api/opportunities/stages/{replied['id']}").status_code == 409  # must choose a target
    r = client.delete(f"/api/opportunities/stages/{replied['id']}", params={"move_to": custom["id"]})
    assert r.status_code == 200 and r.json()["moved"] == 2
    names = {s["name"]: s for s in r.json()["stages"]}
    assert "Replied" not in names and names["Hot"]["count"] == 2
    assert names["Hot"]["lead_status"] == "replied"  # the status link moved to the target
    db.expire_all()
    assert {c.lead_status for c in db.scalars(select(Contact))} == {"replied"}  # leads untouched
    assert len(list(db.scalars(select(CrmOpportunity)))) == 2

    # the last stage can't be deleted
    for s in client.get("/api/opportunities/stages").json()[:-1]:
        target = client.get("/api/opportunities/stages").json()[-1]["id"]
        client.delete(f"/api/opportunities/stages/{s['id']}", params={"move_to": target})
    [last] = client.get("/api/opportunities/stages").json()
    assert client.delete(f"/api/opportunities/stages/{last['id']}").status_code == 409
    assert db.scalar(select(PipelineStage.id))


# --- detail edits ---------------------------------------------------------------------------------

def test_detail_edits_reuse_lead_and_company_and_reject_duplicates(client, db):
    cid = lead(client)
    lead(client, "taken@acmebail.com")
    r = client.patch(f"/api/opportunities/{cid}", json={"first_name": "Annie", "phone": "512-555-0199",
                                                         "company_name": "Lone Star Bonds", "status": "won"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["first_name"] == "Annie" and d["phone"] == "512-555-0199" and d["status"] == "won"
    assert d["company"]["name"] == "Lone Star Bonds"
    # the same business name links to the existing company instead of creating another
    other = lead(client, "c@lonestar.com", company_name="Lone Star Bonds")
    assert client.get(f"/api/opportunities/{other}").json()["company"]["id"] == d["company"]["id"]

    assert client.patch(f"/api/opportunities/{cid}", json={"email": "taken@acmebail.com"}).status_code == 409
    assert client.patch(f"/api/opportunities/{cid}", json={"email": "not-an-email"}).status_code == 422
    assert client.patch(f"/api/opportunities/{cid}", json={"email": "New@AcmeBail.com"}).json()["email"] == "new@acmebail.com"
    acts = client.get(f"/api/opportunities/{cid}/activity").json()
    assert any(a["type"] == "contact_updated" and "phone" in a["summary"] for a in acts)
    assert any(a["type"] == "opportunity_status" for a in acts)


# --- tasks and reminders --------------------------------------------------------------------------

def test_task_crud_timezone_filters_and_history(client):
    cid = lead(client)
    r = client.post(f"/api/opportunities/{cid}/tasks", json={
        "title": "Call about bond", "task_type": "Call", "due_date": "2030-03-10", "due_time": "14:30",
        "timezone": "America/Chicago", "reminder_minutes": 15, "description": "Ask about Harris County"})
    assert r.status_code == 201, r.text
    t = r.json()
    # US daylight time starts 2030-03-10, so 14:30 in Chicago that afternoon is CDT (UTC-5)
    assert t["due_at"].startswith("2030-03-10T19:30") and t["due_time"] == "14:30"
    assert t["reminder_at"].startswith("2030-03-10T19:15") and t["status"] == "open"
    assert client.post(f"/api/opportunities/{cid}/tasks", json={"title": "x", "task_type": "Juggling"}).status_code == 422
    assert client.post(f"/api/opportunities/{cid}/tasks", json={"title": "x", "reminder_minutes": 15}).status_code == 422
    assert client.post(f"/api/opportunities/{cid}/tasks", json={"title": "x", "due_date": "2030-01-01",
                                                                "timezone": "Mars/Base"}).status_code == 422
    past = client.post(f"/api/opportunities/{cid}/tasks", json={"title": "Send invoice", "task_type": "Payment Reminder",
                                                                 "due_date": "2020-01-01"}).json()
    assert past["status"] == "overdue" and past["due_time"] == "09:00"

    assert [x["title"] for x in client.get(f"/api/opportunities/{cid}/tasks", params={"status": "overdue"}).json()] == ["Send invoice"]
    assert [x["title"] for x in client.get(f"/api/opportunities/{cid}/tasks", params={"task_type": "Call"}).json()] == ["Call about bond"]

    r = client.patch(f"/api/opportunities/tasks/{past['id']}", json={"completed": True})
    assert r.json()["status"] == "completed"
    assert client.get(f"/api/opportunities/{cid}/tasks", params={"status": "completed"}).json()[0]["id"] == past["id"]
    assert client.patch(f"/api/opportunities/tasks/{past['id']}", json={"completed": False}).json()["status"] == "overdue"
    r = client.patch(f"/api/opportunities/tasks/{t['id']}", json={"title": "Call Ann", "due_date": "2030-03-11",
                                                                  "due_time": "09:00", "timezone": "America/Chicago"})
    assert r.json()["title"] == "Call Ann" and r.json()["reminder_at"].startswith("2030-03-11T13:45")
    assert client.delete(f"/api/opportunities/tasks/{t['id']}").status_code == 200

    types = [a["type"] for a in client.get(f"/api/opportunities/{cid}/activity").json()]
    for kind in ("task_created", "task_completed", "task_reopened", "task_updated", "task_deleted"):
        assert kind in types
    # the board card shows the next due task
    card = next(i for s in client.get("/api/opportunities").json()["stages"] for i in s["items"] if i["id"] == cid)
    assert card["next_task"]["title"] == "Send invoice" and card["next_task"]["overdue"] is True

    custom = client.put("/api/opportunities/task-types", json={"task_types": ["Call", "Site visit", "Call"]}).json()
    assert custom["task_types"] == ["Call", "Site visit"]


def _task(db, cid, user_email="admin@trace.studio", **kw):
    user = db.scalar(select(User).where(User.email == user_email))
    t = CrmTask(contact_id=cid, title="Call Ann", task_type="Call", timezone="America/Chicago",
                created_by_id=user.id, assignee_id=user.id, **kw)
    db.add(t)
    db.commit()
    return t


def test_reminder_fires_once_then_overdue_once_for_the_owner_only(client, app, db):
    cid = lead(client)
    make_user("other@trace.studio")
    now = utcnow()
    t = _task(db, cid, due_at=now + timedelta(hours=1), reminder_minutes=60, reminder_at=now)

    assert reminders.fire_due(now) == {"reminders": 1, "overdue": 0}
    assert reminders.fire_due(now) == {"reminders": 0, "overdue": 0}  # idempotent
    [n] = db.scalars(select(Notification))
    assert n.kind == "task_reminder" and "Call Ann" in n.title and "Acme Bail" in n.body
    assert n.user_id == db.scalar(select(User.id).where(User.email == "admin@trace.studio"))

    later = now + timedelta(hours=1, minutes=20)
    assert reminders.fire_due(later) == {"reminders": 0, "overdue": 1}
    assert reminders.fire_due(later) == {"reminders": 0, "overdue": 0}

    # completed tasks never remind; a reminder missed while the scheduler was off folds into the overdue notice
    db.get(CrmTask, t.id).completed_at = utcnow()
    db.commit()
    stale = _task(db, cid, due_at=now - timedelta(days=2), reminder_minutes=15, reminder_at=now - timedelta(days=2, minutes=15))
    assert reminders.fire_due(now) == {"reminders": 0, "overdue": 1}
    db.expire_all()
    assert db.get(CrmTask, stale.id).reminder_sent_at is not None

    other = login(app, "other@trace.studio")
    assert other.get("/api/notifications/unread-count").json()["unread"] == 0  # not their task
    assert client.get("/api/notifications/unread-count").json()["unread"] == 3


def test_changing_the_due_time_rearms_the_reminder(client, db):
    cid = lead(client)
    t = client.post(f"/api/opportunities/{cid}/tasks", json={"title": "Follow up", "due_date": "2030-05-01",
                                                             "due_time": "10:00", "timezone": "UTC",
                                                             "reminder_minutes": 0}).json()
    row = db.get(CrmTask, t["id"])
    reminders.fire_due(row.reminder_at)
    db.expire_all()
    assert db.get(CrmTask, t["id"]).reminder_sent_at is not None
    client.patch(f"/api/opportunities/tasks/{t['id']}", json={"due_date": "2030-05-02", "due_time": "10:00",
                                                              "timezone": "UTC"})
    db.expire_all()
    row = db.get(CrmTask, t["id"])
    assert row.reminder_sent_at is None and row.reminder_at.isoformat().startswith("2030-05-02T10:00")
    assert reminders.fire_due(row.reminder_at)["reminders"] == 1


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_reminder_email_goes_only_to_the_notification_address_once(client, db, monkeypatch):
    import app.services.notify_email as ne

    FakeSMTP.sent = []
    monkeypatch.setattr(ne.smtplib, "SMTP", FakeSMTP)
    s = get_settings()
    monkeypatch.setattr(s, "notify_smtp_host", "smtp.example.com")
    monkeypatch.setattr(s, "notify_smtp_from", "reminders@trace.studio")
    cid = lead(client)
    now = utcnow()
    _task(db, cid, due_at=now + timedelta(minutes=15), reminder_minutes=15, reminder_at=now)

    # not configured yet (no notification address): in-app only
    reminders.tick(now)
    assert FakeSMTP.sent == [] and db.scalar(select(Notification.email_status)) == "none"

    client.put("/api/settings/notifications", json={"notification_email": "Me@TraceStudio.dev"})
    _task(db, cid, due_at=now + timedelta(minutes=20), reminder_minutes=15, reminder_at=now + timedelta(minutes=5))
    assert reminders.tick(now + timedelta(minutes=5)) == {"reminders": 1, "emails": 1}
    assert reminders.tick(now + timedelta(minutes=6)) == {}
    [msg] = FakeSMTP.sent
    assert msg["To"] == "me@tracestudio.dev" and msg["From"] == "reminders@trace.studio"
    assert "owner@acmebail.com" not in str(msg["To"])  # never the lead
    assert "Call Ann" in msg["Subject"] and "/opportunities?open=" in msg.get_content()


# --- notes ----------------------------------------------------------------------------------------

def test_notes_edit_history_archive_and_legacy(client, db):
    cid = lead(client)
    legacy = client.post(f"/api/contacts/{cid}/notes", json={"text": "From the lead page"})
    assert legacy.status_code == 201
    n = client.post(f"/api/opportunities/{cid}/notes", json={"body": "First call went well"}).json()
    created = n["created_at"]
    e = client.patch(f"/api/notes/{n['id']}", json={"body": "First call went well. Send pricing."}).json()
    assert e["created_at"] == created and e["edited_at"] and e["revisions"] == 1 and e["edited_by"]
    revs = client.get(f"/api/notes/{n['id']}/revisions").json()
    assert [r["body"] for r in revs] == ["First call went well"]

    listing = client.get(f"/api/opportunities/{cid}/notes").json()
    assert {x["body"] for x in listing} == {"First call went well. Send pricing.", "From the lead page"}
    client.post(f"/api/notes/{n['id']}/archive")
    assert [x["body"] for x in client.get(f"/api/opportunities/{cid}/notes").json()] == ["From the lead page"]
    archived = client.get(f"/api/opportunities/{cid}/notes", params={"include_archived": True}).json()
    assert any(x["archived_at"] for x in archived)
    assert client.patch(f"/api/notes/{n['id']}", json={"body": "nope"}).status_code == 422  # restore first
    client.post(f"/api/notes/{n['id']}/archive", params={"archived": False})
    types = [a["type"] for a in client.get(f"/api/opportunities/{cid}/activity").json()]
    assert {"note", "note_edited", "note_archived", "note_restored"} <= set(types)
    assert client.get(f"/api/opportunities/{cid}").json()["latest_note"]["body"].startswith("First call")


# --- notification center --------------------------------------------------------------------------

def test_notification_center_read_unread_dismiss_and_filters(client, app, db):
    cid = lead(client)
    now = utcnow()
    _task(db, cid, due_at=now + timedelta(hours=2), reminder_minutes=120, reminder_at=now)
    _task(db, cid, due_at=now - timedelta(hours=3))
    reminders.fire_due(now)
    page = client.get("/api/notifications").json()
    assert page["unread"] == 2 and {i["kind"] for i in page["items"]} == {"task_reminder", "task_overdue"}
    item = next(i for i in page["items"] if i["kind"] == "task_reminder")
    assert item["contact_id"] == cid and item["task_type"] == "Call" and item["contact_name"].startswith("Ann Lee")
    assert [i["kind"] for i in client.get("/api/notifications", params={"filter": "overdue"}).json()["items"]] == ["task_overdue"]

    assert client.post(f"/api/notifications/{item['id']}/read", json={"read": True}).json()["unread"] == 1
    assert len(client.get("/api/notifications", params={"filter": "unread"}).json()["items"]) == 1
    assert client.post(f"/api/notifications/{item['id']}/read", json={"read": False}).json()["unread"] == 2
    assert client.post("/api/notifications/read-all").json()["unread"] == 0
    assert client.post(f"/api/notifications/{item['id']}/dismiss").status_code == 200
    assert len(client.get("/api/notifications").json()["items"]) == 1
    assert client.post(f"/api/notifications/{item['id']}/read", json={"read": True}).status_code == 404

    make_user("viewer@trace.studio", role="viewer")
    v = login(app, "viewer@trace.studio")
    other = client.get("/api/notifications").json()["items"][0]["id"]
    assert v.post(f"/api/notifications/{other}/dismiss").status_code == 404  # someone else's notification


def test_legacy_lead_page_task_api_still_works(client):
    cid = lead(client)
    t = client.post("/api/tasks", json={"contact_id": cid, "title": "Old style", "due_at": "2030-01-01T15:00:00Z"})
    assert t.status_code == 201 and t.json()["due_at"].startswith("2030-01-01T15:00")
    assert client.patch(f"/api/tasks/{t.json()['id']}", json={"completed": True}).json()["completed_at"]
    assert client.get(f"/api/opportunities/{cid}/tasks", params={"status": "completed"}).json()[0]["title"] == "Old style"
    assert client.delete(f"/api/tasks/{t.json()['id']}").status_code == 200


def test_prospects_stay_off_the_board_until_added(client, db):
    p = prospect(client, "maria@lonestar.com", first_name="Maria", company_name="Lone Star")
    assert all(not s["items"] for s in client.get("/api/opportunities").json()["stages"])
    assert sum(s["count"] for s in client.get("/api/opportunities/stages").json()) == 0

    # adding by the same email reuses the prospect: no duplicate, blanks filled, existing values kept
    proposal = stage_named(client, "Proposal sent")
    r = client.post("/api/opportunities", json={"email": "Maria@LoneStar.com", "first_name": "Someone else",
                                                "phone": "512-555-0142", "stage_id": proposal["id"], "value_cents": 90000})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["id"] == p and d["reused_prospect"] is True and d["on_board"] is True
    assert d["first_name"] == "Maria" and d["phone"] == "512-555-0142" and d["stage_name"] == "Proposal sent"
    assert d["value_cents"] == 90000
    assert len(list(db.scalars(select(Contact)))) == 1
    assert client.post("/api/opportunities", json={"email": "maria@lonestar.com"}).status_code == 409
    assert column_of(client, p) == "Proposal sent"

    # a brand-new person
    r = client.post("/api/opportunities", json={"email": "new@client.com", "first_name": "Nia", "company_name": "Nia Co"})
    assert r.status_code == 201 and r.json()["reused_prospect"] is False and r.json()["stage_name"] == "New lead"
    assert client.post("/api/opportunities", json={"email": "not-an-email"}).status_code == 422
    acts = [a["type"] for a in client.get(f"/api/opportunities/{p}/activity").json()]
    assert "opportunity_created" in acts


def test_remove_from_board_keeps_everything(client, db):
    cid = lead(client)
    client.post(f"/api/opportunities/{cid}/tasks", json={"title": "Call", "due_date": "2030-01-01"})
    client.post(f"/api/opportunities/{cid}/notes", json={"body": "Keep me"})
    assert client.post(f"/api/opportunities/{cid}/remove").json()["message"].startswith("Removed")
    assert column_of(client, cid) is None
    assert client.post(f"/api/opportunities/{cid}/remove").status_code == 409
    db.expire_all()
    assert db.get(Contact, cid) is not None
    assert len(client.get(f"/api/opportunities/{cid}/tasks").json()) == 1
    assert client.get(f"/api/opportunities/{cid}/notes").json()[0]["body"] == "Keep me"
    assert client.get(f"/api/opportunities/{cid}").json()["on_board"] is False
    # it can be added back, with its history
    assert client.post("/api/opportunities", json={"email": "owner@acmebail.com"}).status_code == 201
    assert column_of(client, cid) == "New lead"

