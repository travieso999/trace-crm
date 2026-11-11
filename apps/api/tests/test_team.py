"""Multi-user workspace: team accounts, permissions, opportunity ownership, filters, bulk assignment, accountability."""

import pathlib
import uuid

from sqlalchemy import create_engine, text

from alembic import command
from alembic.config import Config
from tests.conftest import DB_URL, login, make_user

PW = "Sup3rSecretPass!"
NEW_PW = "An0therGoodPassword"


def add_user(admin, email, name, role="member", username=None, **kw):
    r = admin.post("/api/auth/users", json={"email": email, "full_name": name, "password": PW, "role": role,
                                            **({"username": username} if username else {}), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def opp(client, email, **kw):
    r = client.post("/api/opportunities", json={"email": email, "first_name": email.split("@")[0].title(), **kw})
    assert r.status_code == 201, r.text
    return r.json()


def ids(rows):
    return {r["id"] for r in rows}


def team(app):
    """Travis (admin) plus two employees, each signed in with their own session."""
    make_user(email="travis@trace.studio")
    admin = login(app, "travis@trace.studio")
    admin.patch(f"/api/auth/users/{admin.get('/api/auth/me').json()['user']['id']}", json={"full_name": "Travis"})
    e1 = add_user(admin, "emp1@trace.studio", "Employee One", username="emp1")
    e2 = add_user(admin, "emp2@trace.studio", "Employee Two", username="emp2")
    return admin, e1, e2, login(app, "emp1", PW), login(app, "emp2@trace.studio", PW)


# --- accounts ------------------------------------------------------------------------------------

def test_admin_creates_employees_who_sign_in_independently(app):
    admin, e1, e2, c1, c2 = team(app)
    assert e1["username"] == "emp1" and e1["role"] == "member" and "password_hash" not in e1
    me1, me2 = c1.get("/api/auth/me").json()["user"], c2.get("/api/auth/me").json()["user"]
    assert me1["id"] == e1["id"] and me2["id"] == e2["id"]  # separate accounts and sessions
    assert "users.manage" not in me1["permissions"] and "opportunities.create" in me1["permissions"]
    assert "users.manage" in admin.get("/api/auth/me").json()["user"]["permissions"]
    # username sign-in is case-insensitive; a wrong password is rejected the same way as an unknown user
    login(app, "EMP1", PW)
    from fastapi.testclient import TestClient
    bad = TestClient(app).post("/api/auth/login", json={"email": "emp1", "password": "wrong"})
    assert bad.status_code == 401 and "password" in bad.json()["detail"]
    # duplicates
    assert admin.post("/api/auth/users", json={"email": "EMP1@trace.studio", "password": PW}).status_code == 409
    assert admin.post("/api/auth/users", json={"email": "x@trace.studio", "username": "Emp1", "password": PW}).status_code == 409
    assert admin.post("/api/auth/users", json={"email": "y@trace.studio", "username": "a@b", "password": PW}).status_code == 422
    listed = admin.get("/api/auth/users").json()
    assert {u["email"] for u in listed} == {"travis@trace.studio", "emp1@trace.studio", "emp2@trace.studio"}


def test_team_members_cannot_reach_admin_endpoints(app):
    admin, e1, _, c1, _ = team(app)
    assert c1.get("/api/auth/users").status_code == 403
    assert c1.post("/api/auth/users", json={"email": "z@trace.studio", "password": PW}).status_code == 403
    assert c1.patch(f"/api/auth/users/{e1['id']}", json={"role": "admin"}).status_code == 403
    assert c1.post(f"/api/auth/users/{e1['id']}/password", json={"new_password": NEW_PW}).status_code == 403
    assert c1.delete(f"/api/auth/users/{e1['id']}").status_code == 403
    assert c1.put("/api/settings/company", json={"name": "Hacked"}).status_code == 403
    assert c1.post("/api/opportunities/stages", json={"name": "X"}).status_code == 403
    assert c1.post("/api/email-accounts/sandbox", json={"email": "a@b.co"}).status_code in (403, 404)
    # the teammate list is readable (for owner pickers) and carries no secrets
    roster = c1.get("/api/auth/team").json()
    assert len(roster) == 3 and all(set(r) == {"id", "full_name", "username", "email", "role", "is_active"} for r in roster)


def test_edit_reset_password_deactivate_and_reactivate(app):
    admin, e1, _, c1, _ = team(app)
    r = admin.patch(f"/api/auth/users/{e1['id']}", json={"full_name": "Emp Uno", "email": "uno@trace.studio", "username": "uno"})
    assert r.status_code == 200 and r.json()["email"] == "uno@trace.studio"
    login(app, "uno", PW)
    # admin reset signs them out everywhere; the new password works, the old one does not
    assert admin.post(f"/api/auth/users/{e1['id']}/password", json={"new_password": "short"}).status_code == 422
    assert admin.post(f"/api/auth/users/{e1['id']}/password", json={"new_password": NEW_PW}).status_code == 200
    assert c1.get("/api/auth/me").status_code == 401
    c1 = login(app, "uno", NEW_PW)
    # deactivation ends the session immediately and blocks sign-in
    assert admin.patch(f"/api/auth/users/{e1['id']}", json={"is_active": False}).status_code == 200
    assert c1.get("/api/opportunities").status_code == 401
    from fastapi.testclient import TestClient
    assert TestClient(app).post("/api/auth/login", json={"email": "uno", "password": NEW_PW}).status_code == 401
    assert admin.patch(f"/api/auth/users/{e1['id']}", json={"is_active": True}).status_code == 200
    login(app, "uno", NEW_PW)


def test_admin_safety_rails(app):
    admin, e1, _, _, _ = team(app)
    me = admin.get("/api/auth/me").json()["user"]["id"]
    assert admin.delete(f"/api/auth/users/{me}").status_code == 400
    assert admin.patch(f"/api/auth/users/{me}", json={"role": "member"}).status_code == 400
    # a second admin can be demoted, the last one cannot
    admin.patch(f"/api/auth/users/{e1['id']}", json={"role": "admin"})
    assert admin.patch(f"/api/auth/users/{e1['id']}", json={"role": "member"}).status_code == 200


def test_delete_user_reassigns_work_keeps_history_and_frees_the_email(app):
    admin, e1, e2, c1, _ = team(app)
    a = opp(c1, "a@client.com")["id"]
    opp(c1, "b@client.com")
    c1.post(f"/api/opportunities/{a}/notes", json={"body": "Called them"})
    r = admin.delete(f"/api/auth/users/{e1['id']}", params={"reassign_to": e2["id"]})
    assert r.status_code == 200 and "2 opportunities" in r.json()["message"]
    assert c1.get("/api/auth/me").status_code == 401
    rows = admin.get("/api/opportunities/list", params={"owner": e2["id"]}).json()["items"]
    assert len(rows) == 2 and all(r["owner"]["name"] == "Employee Two" for r in rows)
    # history still names the deleted employee
    notes = admin.get(f"/api/opportunities/{a}/notes").json()
    assert notes[0]["author"] == "Employee One"
    history = admin.get(f"/api/opportunities/{a}/activity", params={"type": "owner_change"}).json()
    assert history[0]["summary"] == "Assigned to Employee Two (was Employee One)" and history[0]["actor"] == "Travis"
    assert e1["id"] not in ids(admin.get("/api/auth/users").json()) and e1["id"] not in ids(admin.get("/api/auth/team").json())
    # the address and username can be used for a new hire
    add_user(admin, "emp1@trace.studio", "New Hire", username="emp1")
    # deleting without a target leaves their work unassigned
    e3 = add_user(admin, "emp3@trace.studio", "Three")
    c3 = login(app, "emp3@trace.studio", PW)
    opp(c3, "c@client.com")
    admin.delete(f"/api/auth/users/{e3['id']}")
    assert admin.get("/api/opportunities/list", params={"owner": "unassigned"}).json()["total"] == 1


# --- ownership -----------------------------------------------------------------------------------

def test_every_opportunity_shows_its_owner_and_creator(app):
    admin, e1, e2, c1, c2 = team(app)
    a = opp(admin, "a@client.com")
    assert a["owner"]["name"] == "Travis" and a["accountability"]["created_by"]["name"] == "Travis"
    b = opp(admin, "b@client.com", owner_id=e1["id"])
    assert b["owner"]["id"] == e1["id"] and b["accountability"]["owner_assigned_by"]["name"] == "Travis"
    u = opp(admin, "u@client.com", owner_id=None)
    assert u["owner"] is None
    # members create for themselves or unassigned, never for a teammate
    assert opp(c1, "c@client.com")["owner"]["id"] == e1["id"]
    assert opp(c1, "d@client.com", owner_id=None)["owner"] is None
    r = c1.post("/api/opportunities", json={"email": "e@client.com", "owner_id": e2["id"]})
    assert r.status_code == 403
    # inactive teammates can't receive new work
    admin.patch(f"/api/auth/users/{e2['id']}", json={"is_active": False})
    r = admin.patch(f"/api/opportunities/{a['id']}", json={"owner_id": e2["id"]})
    assert r.status_code == 422 and "deactivated" in r.json()["detail"]
    # board cards carry the owner too
    assert c2.get("/api/opportunities").status_code == 401  # deactivated just above
    cards = {i["email"]: i["owner"] for s in c1.get("/api/opportunities").json()["stages"] for i in s["items"]}
    assert cards["u@client.com"] is None and cards["a@client.com"]["name"] == "Travis"


def test_assigning_rules_and_ownership_history(app):
    admin, e1, e2, c1, c2 = team(app)
    free = opp(admin, "free@client.com", owner_id=None)["id"]
    travis = opp(admin, "travis@client.com")["id"]
    # a member takes an unassigned opportunity...
    r = c1.patch(f"/api/opportunities/{free}", json={"owner_id": e1["id"]})
    assert r.status_code == 200 and r.json()["owner"]["name"] == "Employee One" and r.json()["can"]["release"]
    # ...but cannot take or hand off a teammate's
    assert c2.patch(f"/api/opportunities/{free}", json={"owner_id": e2["id"]}).status_code == 403
    assert c1.patch(f"/api/opportunities/{travis}", json={"owner_id": e1["id"]}).status_code == 403
    assert c1.patch(f"/api/opportunities/{free}", json={"owner_id": e2["id"]}).status_code == 403
    # the admin reassigns anything
    r = admin.patch(f"/api/opportunities/{free}", json={"owner_id": e2["id"]})
    acc = r.json()["accountability"]
    assert acc["owner_assigned_by"]["name"] == "Travis" and acc["updated_by"]["name"] == "Travis" and acc["owner_assigned_at"]
    c2.post(f"/api/opportunities/{free}/notes", json={"body": "Left a voicemail"})
    assert admin.get(f"/api/opportunities/{free}").json()["accountability"]["updated_by"]["name"] == "Employee Two"
    admin.patch(f"/api/opportunities/{free}", json={"owner_id": None})
    history = admin.get(f"/api/opportunities/{free}/activity", params={"type": "owner_change"}).json()
    assert [(h["actor"], h["summary"]) for h in reversed(history)] == [
        ("Employee One", "Assigned to Employee One"),
        ("Travis", "Assigned to Employee Two (was Employee One)"),
        ("Travis", "Unassigned (was Employee Two)"),
    ]
    assert history[0]["data"] == {"from_owner": "Employee Two", "to_owner": "Unassigned"}


def test_bulk_assignment(app):
    admin, e1, _, c1, _ = team(app)
    rows = [opp(admin, f"p{i}@client.com", owner_id=None)["id"] for i in range(3)]
    r = admin.post("/api/opportunities/bulk-assign", json={"ids": [*rows, str(uuid.uuid4())], "owner_id": e1["id"]})
    assert r.json() == {"updated": 3, "matched": 3, "skipped": 1}
    assert admin.get("/api/opportunities/list", params={"owner": e1["id"]}).json()["total"] == 3
    assert c1.post("/api/opportunities/bulk-assign", json={"ids": rows, "owner_id": None}).status_code == 403
    # an admin can grant "assign to anyone" to one employee
    admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"opportunities.reassign": True}})
    assert c1.post("/api/opportunities/bulk-assign", json={"ids": rows, "owner_id": None}).json()["updated"] == 3


# --- editing permissions -------------------------------------------------------------------------

def test_teammate_editing_follows_permissions(app):
    admin, e1, e2, c1, c2 = team(app)
    theirs = opp(c2, "theirs@client.com")["id"]
    mine = opp(c1, "mine@client.com")["id"]
    stage = admin.get("/api/opportunities/stages").json()[2]["id"]
    # by default the team collaborates on everything
    assert c1.patch(f"/api/opportunities/{theirs}", json={"phone": "555"}).status_code == 200
    # with "edit teammates' opportunities" switched off, only own and unassigned ones are editable
    r = admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"opportunities.edit_others": False}})
    assert r.json()["permission_overrides"] == {"opportunities.edit_others": False}
    assert "opportunities.edit_others" not in r.json()["permissions"]
    assert c1.patch(f"/api/opportunities/{theirs}", json={"phone": "556"}).status_code == 403
    assert c1.post(f"/api/opportunities/{theirs}/move", json={"stage_id": stage}).status_code == 403
    assert c1.post(f"/api/opportunities/{theirs}/notes", json={"body": "x"}).status_code == 403
    assert c1.post(f"/api/opportunities/{theirs}/tasks", json={"title": "x"}).status_code == 403
    assert c1.get(f"/api/opportunities/{theirs}").json()["can"]["edit"] is False
    assert c1.get(f"/api/opportunities/{theirs}").status_code == 200  # still visible
    assert c1.patch(f"/api/opportunities/{mine}", json={"phone": "557"}).status_code == 200
    task = c2.post(f"/api/opportunities/{theirs}/tasks", json={"title": "Call back"}).json()
    assert task["created_by"] == "Employee Two" and task["assignee"] == "Employee Two"
    assert c1.patch(f"/api/opportunities/tasks/{task['id']}", json={"completed": True}).status_code == 403
    # removing from the board: own yes, teammate's no (admins always)
    assert c1.post(f"/api/opportunities/{theirs}/remove").status_code == 403
    assert c1.post(f"/api/opportunities/{mine}/remove").status_code == 200
    assert admin.post(f"/api/opportunities/{theirs}/remove").status_code == 200


def test_permission_overrides_validation_and_viewer(app):
    admin, e1, _, _, _ = team(app)
    r = admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"users.manage": True}})
    assert r.status_code == 422
    # switching back to the role default stores nothing
    r = admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"opportunities.edit_others": True}})
    assert r.json()["permission_overrides"] == {}
    # admins always have everything
    me = admin.get("/api/auth/me").json()["user"]
    assert admin.patch(f"/api/auth/users/{me['id']}", json={"permission_overrides": {"inbox.access": False}}).json()[
        "permission_overrides"] == {}
    cat = admin.get("/api/auth/permissions").json()
    assert {r["key"] for r in cat["roles"]} == {"admin", "member", "viewer"}
    viewer = add_user(admin, "v@trace.studio", "Viewer", role="viewer")
    cv = login(app, "v@trace.studio", PW)
    assert viewer["permissions"] == ["inbox.access", "insights.access", "opportunities.view", "outreach.access"]
    assert cv.post("/api/opportunities", json={"email": "v@client.com"}).status_code == 403


def test_outreach_and_insights_are_off_for_team_members_until_switched_on(app):
    admin, e1, _, c1, c2 = team(app)
    me = c1.get("/api/auth/me").json()["user"]["permissions"]
    assert "outreach.access" not in me and "insights.access" not in me
    refused = ["/api/campaigns", "/api/templates", "/api/imports", "/api/email-accounts", "/api/video-assets",
               "/api/inbox", "/api/inbox/unread-count", "/api/analytics"]
    for url in refused:
        assert c1.get(url).status_code == 403, url
    assert c1.post("/api/campaigns", json={"name": "Mine"}).status_code == 403
    # Sales stays available
    assert c1.get("/api/opportunities").status_code == 200 and c1.get("/api/contacts").status_code == 200
    # the admin always has both; switching them on for one employee opens the sections for that person only
    assert admin.get("/api/campaigns").status_code == 200
    admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"outreach.access": True, "insights.access": True}})
    for url in ("/api/campaigns", "/api/imports", "/api/email-accounts", "/api/inbox"):
        assert c1.get(url).status_code == 200, url
    assert c2.get("/api/campaigns").status_code == 403
    # the Google OAuth callback is not behind the switch (it validates its own state)
    assert c2.get("/api/email-accounts/oauth/google/callback").status_code != 403


def test_inbox_and_export_can_be_switched_off_per_person(app):
    admin, e1, e2, c1, c2 = team(app)
    assert c1.get("/api/contacts/export").status_code == 403  # off for team members by default
    for e in (e1, e2):
        admin.patch(f"/api/auth/users/{e['id']}", json={"permission_overrides": {"outreach.access": True,
                                                                               "prospects.export": True}})
    assert c1.get("/api/inbox").status_code == 200 and c1.get("/api/contacts/export").status_code == 200
    admin.patch(f"/api/auth/users/{e1['id']}", json={"permission_overrides": {"outreach.access": True, "inbox.access": False,
                                                                           "prospects.export": False}})
    assert c1.get("/api/inbox").status_code == 403 and c1.get("/api/inbox/unread-count").status_code == 403
    assert c1.get("/api/contacts/export").status_code == 403
    assert c2.get("/api/inbox").status_code == 200


# --- filters -------------------------------------------------------------------------------------

def test_filters_combine_and_work_on_board_and_table(app):
    admin, e1, e2, c1, _ = team(app)
    t_open = opp(admin, "t-open@client.com", company_name="ABC Bail Bonds")["id"]
    t_won = opp(admin, "t-won@client.com")["id"]
    admin.patch(f"/api/opportunities/{t_won}", json={"status": "won"})
    e1_open = opp(admin, "e1@client.com", owner_id=e1["id"])["id"]
    free = opp(admin, "free@client.com", owner_id=None)["id"]
    travis = admin.get("/api/auth/me").json()["user"]["id"]

    def table(client=admin, **params):
        r = client.get("/api/opportunities/list", params=params)
        assert r.status_code == 200, r.text
        return ids(r.json()["items"])

    def board(client=admin, **params):
        return {i["id"] for s in client.get("/api/opportunities", params=params).json()["stages"] for i in s["items"]}

    assert table() == {t_open, t_won, e1_open, free}
    assert table(owner=travis, status="open") == {t_open} == board(owner=travis, status="open")
    assert table(owner="me") == {t_open, t_won}
    assert table(c1, owner="me") == {e1_open} == board(c1, owner="me")
    assert table(c1) == {t_open, t_won, e1_open, free}  # employees still see the whole team's work
    assert table(owner="unassigned") == {free}
    assert table(owner=e2["id"]) == set()
    assert table(q="employee one") == {e1_open}  # search matches the owner's name
    assert table(q="abc bail", owner=travis) == {t_open}
    new_lead = admin.get("/api/opportunities/stages").json()[0]["id"]
    contacted = admin.get("/api/opportunities/stages").json()[1]["id"]
    assert table(stage_id=new_lead, status="won") == {t_won} == board(stage_id=new_lead, status="won")
    assert table(stage_id=contacted) == set()
    assert table(created_from="2000-01-01", created_to="2099-12-31", activity_from="2000-01-01") == {t_open, t_won, e1_open, free}
    assert table(created_to="2000-01-01") == set() and table(activity_to="2000-01-01") == set()
    assert admin.get("/api/opportunities/list", params={"owner": "nobody"}).status_code == 422

    # sorting by owner puts Unassigned last; paging works
    page = admin.get("/api/opportunities/list", params={"sort": "owner", "order": "asc", "page_size": 2}).json()
    assert page["total"] == 4 and [r["owner"]["name"] for r in page["items"]] == ["Employee One", "Travis"]
    last = admin.get("/api/opportunities/list", params={"sort": "owner", "order": "asc", "page": 2, "page_size": 2}).json()
    assert last["items"][-1]["owner"] is None and last["items"][-1]["last_activity_at"]
    for key in ("name", "value", "activity", "stage", "created", "updated"):
        assert admin.get("/api/opportunities/list", params={"sort": key}).status_code == 200


# --- migration -----------------------------------------------------------------------------------

def test_migration_keeps_existing_owners_and_backfills_history(app):
    """Existing opportunities keep their owner (or stay Unassigned); created/updated-by come from history."""
    api_dir = pathlib.Path(__file__).resolve().parent.parent
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", DB_URL)
    cfg.attributes["skip_logging"] = True
    command.downgrade(cfg, "d7a1e5c30b48")
    engine = create_engine(DB_URL)
    try:
        with engine.begin() as c:
            u = c.execute(text("INSERT INTO users (id, email, full_name, password_hash, role, is_active, created_at, updated_at) "
                               "VALUES (gen_random_uuid(), 'old@x.co', 'Travis', 'x', 'admin', true, now(), now()) RETURNING id")).scalar()
            for email, owner in (("owned@x.co", u), ("orphan@x.co", None)):
                cid = c.execute(text("INSERT INTO contacts (id, email, verification_status, lead_status, custom_fields, created_at, updated_at) "
                                     "VALUES (gen_random_uuid(), :e, 'unknown', 'new_lead', '{}', now(), now()) RETURNING id"), {"e": email}).scalar()
                c.execute(text("INSERT INTO crm_opportunities (id, contact_id, owner_id, status, on_board, created_at, updated_at) "
                               "VALUES (gen_random_uuid(), :c, :o, 'open', true, now(), now())"), {"c": cid, "o": owner})
                if owner:
                    c.execute(text("INSERT INTO crm_activities (id, contact_id, user_id, type, summary, data, created_at) "
                                   "VALUES (gen_random_uuid(), :c, :u, 'opportunity_created', 'x', '{}', now())"), {"c": cid, "u": u})
        command.upgrade(cfg, "head")
        with engine.begin() as c:
            rows = {r.email: r for r in c.execute(text(
                "SELECT ct.email, o.owner_id, o.created_by_id, o.updated_by_id, o.last_edited_at FROM crm_opportunities o "
                "JOIN contacts ct ON ct.id = o.contact_id"))}
            overrides = c.execute(text("SELECT permission_overrides FROM users")).scalar()
        assert rows["owned@x.co"].owner_id == u and rows["owned@x.co"].created_by_id == u
        assert rows["owned@x.co"].updated_by_id == u and rows["owned@x.co"].last_edited_at is not None
        assert rows["orphan@x.co"].owner_id is None and rows["orphan@x.co"].created_by_id is None
        assert overrides == {}
    finally:
        command.upgrade(cfg, "head")
        engine.dispose()


# --- assignment notifications ----------------------------------------------------------------------

def bell(client, kind="assignment"):
    return [n for n in client.get("/api/notifications").json()["items"] if n["kind"] == kind]


def test_new_owner_is_notified_once_and_never_about_their_own_work(app):
    admin, e1, e2, c1, c2 = team(app)
    a = opp(admin, "a@client.com", company_name="ABC Bail Bonds", owner_id=e1["id"])["id"]
    n = bell(c1)
    assert len(n) == 1 and n[0]["title"] == "Travis assigned you A (ABC Bail Bonds)" and n[0]["contact_id"] == a
    assert bell(admin) == []
    # taking an unassigned one yourself: no notification
    free = opp(admin, "free@client.com", owner_id=None)["id"]
    c2.patch(f"/api/opportunities/{free}", json={"owner_id": e2["id"]})
    assert bell(c2) == []
    # reassigning notifies the new owner only
    admin.patch(f"/api/opportunities/{a}", json={"owner_id": e2["id"]})
    assert len(bell(c2)) == 1 and len(bell(c1)) == 1
    # unassigning notifies nobody
    admin.patch(f"/api/opportunities/{a}", json={"owner_id": None})
    assert len(bell(c2)) == 1


def test_bulk_and_handover_send_one_summary(app):
    admin, e1, e2, c1, c2 = team(app)
    rows = [opp(admin, f"p{i}@client.com", owner_id=None)["id"] for i in range(7)]
    admin.post("/api/opportunities/bulk-assign", json={"ids": rows, "owner_id": e1["id"]})
    n = bell(c1)
    assert len(n) == 1 and n[0]["title"] == "Travis assigned you 7 opportunities" and n[0]["contact_id"] is None
    assert n[0]["body"].endswith("and 2 more")
    # deleting Employee One hands their 7 to Employee Two in one notification
    admin.delete(f"/api/auth/users/{e1['id']}", params={"reassign_to": e2["id"]})
    assert [x["title"] for x in bell(c2)] == ["Travis assigned you 7 opportunities"]
