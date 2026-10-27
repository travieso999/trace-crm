from fastapi.testclient import TestClient

from tests.conftest import login, make_user


def test_login_logout_me(app):
    make_user()
    c = TestClient(app)
    assert c.get("/api/auth/me").status_code == 401
    r = c.post("/api/auth/login", json={"email": "ADMIN@trace.studio", "password": "Sup3rSecretPass!"})
    assert r.status_code == 200
    assert "ts_session" in r.cookies
    set_cookie = r.headers.get_list("set-cookie")
    assert any("ts_session" in h and "HttpOnly" in h and "SameSite=lax" in h for h in set_cookie)
    csrf = r.json()["csrf_token"]
    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["email"] == "admin@trace.studio"
    assert "password_hash" not in me.text
    # unsafe method without CSRF header is rejected
    assert c.post("/api/auth/logout").status_code == 403
    assert c.post("/api/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get("/api/auth/me").status_code == 401


def test_wrong_password_and_unknown_user(app):
    make_user()
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "admin@trace.studio", "password": "nope"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "ghost@trace.studio", "password": "nope"}).status_code == 401


def test_password_hash_is_argon2id(db):
    from app.core.security import hash_password

    assert hash_password("Abcdefgh1234").startswith("$argon2id$")


def test_login_rate_limit(app, monkeypatch):
    from app.core.config import get_settings

    make_user()
    monkeypatch.setattr(get_settings(), "login_rate_limit_per_minute", 3)
    c = TestClient(app)
    codes = [c.post("/api/auth/login", json={"email": "admin@trace.studio", "password": "bad"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429


def test_rbac_viewer_cannot_write(app):
    make_user(email="viewer@trace.studio", role="viewer")
    v = login(app, "viewer@trace.studio")
    assert v.get("/api/contacts").status_code == 200
    assert v.post("/api/contacts", json={"email": "x@example.com"}).status_code == 403
    assert v.get("/api/auth/users").status_code == 403


def test_admin_user_management(client):
    r = client.post("/api/auth/users", json={"email": "m@trace.studio", "password": "weak", "role": "member"})
    assert r.status_code == 422
    r = client.post("/api/auth/users", json={"email": "m@trace.studio", "password": "GoodPassword123", "role": "member"})
    assert r.status_code == 201
    users = client.get("/api/auth/users").json()
    me = next(u for u in users if u["email"] == "admin@trace.studio")
    # cannot demote the last admin (yourself)
    assert client.patch(f"/api/auth/users/{me['id']}", json={"role": "member"}).status_code == 400


def test_session_revoked_on_deactivate(app, client):
    r = client.post("/api/auth/users", json={"email": "m2@trace.studio", "password": "GoodPassword123"})
    uid = r.json()["id"]
    m = login(app, "m2@trace.studio", "GoodPassword123")
    assert m.get("/api/auth/me").status_code == 200
    client.patch(f"/api/auth/users/{uid}", json={"is_active": False})
    assert m.get("/api/auth/me").status_code == 401


def test_security_headers(client):
    r = client.get("/api/auth/me")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "no-store" in r.headers["cache-control"]
