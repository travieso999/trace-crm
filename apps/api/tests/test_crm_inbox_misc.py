from app.models import CrmActivity, EmailEvent


def _contact(client, email="lead@acme.com", **kw):
    r = client.post("/api/contacts", json={"email": email, "first_name": "Lee", **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_pipeline_opportunity_and_tasks(client, db):
    cid = _contact(client)
    board = client.get("/api/pipeline").json()["stages"]
    assert [s["stage"] for s in board][:2] == ["new_lead", "contacted"]
    assert board[0]["count"] == 1 and board[0]["items"][0]["id"] == cid

    r = client.put(f"/api/contacts/{cid}/opportunity", json={"value_cents": 250000,
                                                            "meeting_at": "2026-11-03T15:00:00Z"})
    assert r.status_code == 200 and r.json()["value_cents"] == 250000
    assert client.get(f"/api/contacts/{cid}").json()["lead_status"] == "meeting_scheduled"
    assert db.query(EmailEvent).filter_by(type="meeting_booked").count() == 1
    stage = next(s for s in client.get("/api/pipeline").json()["stages"] if s["stage"] == "meeting_scheduled")
    assert stage["value_cents"] == 250000 and stage["items"][0]["opportunity"]["value_cents"] == 250000

    # drag-and-drop on the board is a plain lead_status update
    assert client.patch(f"/api/contacts/{cid}", json={"lead_status": "proposal_sent"}).status_code == 200

    t = client.post("/api/tasks", json={"contact_id": cid, "title": "Send proposal", "due_at": "2026-11-05T15:00:00Z"})
    assert t.status_code == 201
    tid = t.json()["id"]
    assert [x["title"] for x in client.get("/api/tasks").json()] == ["Send proposal"]
    assert client.patch(f"/api/tasks/{tid}", json={"completed": True}).json()["completed_at"]
    assert client.get("/api/tasks").json() == []
    assert len(client.get("/api/tasks", params={"open": False}).json()) == 1
    assert {a.type for a in db.query(CrmActivity).filter(CrmActivity.type.like("task%"))} == {"task_created", "task_completed"}


def test_suppression_api(client, db):
    _contact(client, "x@acme.com")
    r = client.post("/api/suppressions", json={"emails": ["X@acme.com", "x@acme.com", "bad", "y@acme.com"],
                                               "reason": "manual"}).json()
    assert r["added"] == 2 and r["invalid"] == ["bad"]
    assert client.post("/api/suppressions", json={"emails": ["y@acme.com"]}).json()["already_suppressed"] == 1
    lst = client.get("/api/suppressions", params={"q": "acme"}).json()
    assert lst["total"] == 2
    assert client.get("/api/contacts", params={"q": "x@acme"}).json()["items"][0]["suppressed"] is True
    entry = lst["items"][0]["id"]
    assert client.delete(f"/api/suppressions/{entry}").status_code == 200
    assert client.get("/api/suppressions").json()["total"] == 1


def test_templates_and_analytics_shape(client):
    t = client.post("/api/templates", json={"name": "Intro", "subject": "Hi {{first_name}}", "body": "Hello"}).json()
    assert client.put(f"/api/templates/{t['id']}", json={"name": "Intro v2", "subject": "s", "body": "b"}).json()["name"] \
        == "Intro v2"
    assert [x["name"] for x in client.get("/api/templates").json()] == ["Intro v2"]
    assert client.delete(f"/api/templates/{t['id']}").status_code == 200

    a = client.get("/api/analytics", params={"days": 7}).json()
    assert len(a["daily"]) == 7 and a["summary"]["reply_rate"] == 0.0 and a["has_data"] is False
    assert {"accepted", "replied", "bounced"} <= set(a["daily"][0])

    v = client.get("/api/campaigns/variables").json()
    assert "first_name" in v["standard"]
