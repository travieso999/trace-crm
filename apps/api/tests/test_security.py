"""Security regression tests: every route's access rules, CSRF, IDOR, exports, lockout, sessions, two-step
verification, the append-only audit log and production configuration. New routes are covered automatically by the
sweeps; a deliberately public route must be added to PUBLIC."""

import time
import uuid
from datetime import timedelta

import pyotp
import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.core import ratelimit
from app.core.crypto import decrypt
from app.models import AuditLog, Contact, Notification, User, UserSession
from app.models.base import utcnow
from tests.conftest import login, make_user

PASSWORD = "Sup3rSecretPass!"
# routes that work without a session, on purpose
PUBLIC = {("POST", "/api/auth/login"), ("POST", "/api/auth/login/mfa"), ("GET", "/api/health"),
          ("GET", "/api/email-accounts/oauth/google/callback"), ("GET", "/u/{token}"), ("POST", "/u/{token}")}
# writes any signed-in user may make about themselves
SELF_SERVICE = {"/api/auth/logout", "/api/auth/password", "/api/auth/sessions/revoke-others", "/api/auth/mfa/setup",
                "/api/auth/mfa/enable", "/api/auth/mfa/disable", "/api/auth/mfa/recovery-codes"}
# POSTs that only compute something from the request body and change nothing
READ_ONLY_POSTS = {"/api/campaigns/preview"}


def _routes(app):
    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):  # included routers are wrapped
                yield from walk(r.original_router.routes)
            elif isinstance(r, APIRoute):
                yield r

    for r in walk(app.routes):
        for m in r.methods - {"HEAD", "OPTIONS"}:
            yield m, r.path


def _fill(path: str) -> str:
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + str(uuid.uuid4()) + out[end + 1:]
    return out


def test_every_route_requires_a_session(app):
    anon = TestClient(app)
    checked = 0
    for method, path in _routes(app):
        if (method, path) in PUBLIC:
            continue
        r = anon.request(method, _fill(path), json={})
        assert r.status_code == 401, f"{method} {path} answered {r.status_code} without a session"
        checked += 1
    assert checked > 100


def test_viewer_cannot_change_anything(app):
    make_user(email="viewer@trace.studio", role="viewer")
    v = login(app, "viewer@trace.studio")
    for method, path in _routes(app):
        if (method == "GET" or (method, path) in PUBLIC or path in SELF_SERVICE | READ_ONLY_POSTS
                or path.startswith("/api/notifications")):
            continue
        r = getattr(v, method.lower())(_fill(path), **({} if method == "DELETE" else {"json": {}}))
        assert r.status_code == 403, f"viewer {method} {path} -> {r.status_code}"


def test_member_cannot_use_admin_routes(app):
    make_user(email="member@trace.studio", role="member")
    m = login(app, "member@trace.studio")
    admin_only = [("get", "/api/auth/users"), ("get", "/api/auth/audit-log"), ("get", "/api/contacts/export"),
                  ("put", "/api/settings/company"), ("post", "/api/email-accounts/oauth/google/start"),
                  ("delete", f"/api/email-accounts/{uuid.uuid4()}"), ("post", f"/api/auth/users/{uuid.uuid4()}/mfa/reset")]
    for method, path in admin_only:
        kw = {} if method in ("get", "delete") else {"json": {}}
        assert getattr(m, method)(path, **kw).status_code == 403, path


def test_every_write_needs_the_csrf_token(app, client):
    raw = client.c  # same session cookie, but no X-CSRF-Token header
    for method, path in _routes(app):
        if method == "GET" or (method, path) in PUBLIC:
            continue
        r = raw.request(method, _fill(path), json={})
        assert r.status_code == 403 and "CSRF" in r.json()["detail"], f"{method} {path} -> {r.status_code}"


def test_notifications_are_private_to_their_owner(app, client, db):
    owner = db.scalar(select(User).where(User.email == "admin@trace.studio"))
    n = Notification(user_id=owner.id, kind="security", title="private", fire_at=utcnow())
    db.add(n)
    db.commit()
    make_user(email="other@trace.studio", role="admin")
    other = login(app, "other@trace.studio")
    assert other.post(f"/api/notifications/{n.id}/read", json={"read": True}).status_code == 404
    assert other.post(f"/api/notifications/{n.id}/dismiss").status_code == 404
    assert all(i["title"] != "private" for i in other.get("/api/notifications").json()["items"])
    assert client.post(f"/api/notifications/{n.id}/read", json={"read": True}).status_code == 200


def test_export_is_admin_only_logged_alerted_and_limited(app, client, db, monkeypatch):
    db.add(Contact(email="lead@example.com", first_name="Lee"))
    db.commit()
    make_user(email="viewer@trace.studio", role="viewer")
    assert login(app, "viewer@trace.studio").get("/api/contacts/export").status_code == 403
    r = client.get("/api/contacts/export?state=TX")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "contacts.exported"))
    assert entry.data["filters"] == {"state": "TX"}
    assert db.scalar(select(Notification).where(Notification.kind == "security",
                                                Notification.title.startswith("Prospects exported")))
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "export_limit_per_hour", 1)
    ratelimit.reset()
    assert client.get("/api/contacts/export").status_code == 200
    assert client.get("/api/contacts/export").status_code == 429


def test_repeated_failed_sign_ins_lock_the_account(app, client, db):
    anon = TestClient(app)
    for _ in range(8):
        assert anon.post("/api/auth/login", json={"email": "admin@trace.studio", "password": "wrong"}).status_code == 401
    # locked: even the right password is refused, and the admins hear about it
    r = anon.post("/api/auth/login", json={"email": "admin@trace.studio", "password": PASSWORD})
    assert r.status_code == 429
    assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.locked_out"))
    assert db.scalar(select(Notification).where(Notification.title.startswith("Sign-in locked")))
    # another account is unaffected
    make_user(email="second@trace.studio")
    assert anon.post("/api/auth/login", json={"email": "second@trace.studio", "password": PASSWORD}).status_code == 200


def test_idle_session_expires(app, client, db):
    assert client.get("/api/auth/me").status_code == 200
    db.execute(update(UserSession).values(last_seen_at=utcnow() - timedelta(hours=3)))
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_permission_probing_alerts_admins(app, client, db):
    make_user(email="viewer@trace.studio", role="viewer")
    v = login(app, "viewer@trace.studio")
    for _ in range(11):
        v.get("/api/auth/users")
    assert db.scalar(select(AuditLog).where(AuditLog.action == "authz.denied"))
    assert db.scalar(select(Notification).where(Notification.title.startswith("Repeated permission errors")))


def test_responses_never_carry_secrets(client):
    body = client.get("/api/auth/me").text + client.get("/api/auth/users").text + client.get("/api/email-accounts").text
    for word in ("password_hash", "totp_secret", "recovery_code", "token_enc", "refresh_token", "access_token"):
        assert word not in body


def test_unexpected_errors_do_not_leak_details(app, client, monkeypatch):
    from app.services import analytics

    def boom(*a, **kw):
        raise RuntimeError("secret internal detail at /srv/app.py line 3")

    monkeypatch.setattr(analytics, "event_filters", boom)
    plain = TestClient(app, raise_server_exceptions=False)
    plain.cookies = client.c.cookies
    r = plain.get("/api/analytics")
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error"}


def test_unsubscribe_rejects_forged_tokens(app):
    anon = TestClient(app)
    assert anon.get("/u/..%2F..%2Fetc%2Fpasswd").status_code == 404  # never reaches a handler
    for token in ("abc.def", "x" * 300, "eyJ0ZXN0IjoxfQ.AAAAAAAAAAAAAAAAAAAAAA"):
        for r in (anon.get(f"/u/{token}"), anon.post(f"/u/{token}")):
            assert r.status_code == 404 and "invalid" in r.text
            assert r.headers["x-robots-tag"] == "noindex" and "default-src 'none'" in r.headers["content-security-policy"]


def test_inbound_html_is_sanitised():
    import nh3

    dirty = ('<p onclick="x()">hi</p><script>alert(1)</script><img src=x onerror=alert(1)>'
             '<a href="javascript:alert(1)">x</a><iframe src="https://evil"></iframe>')
    clean = nh3.clean(dirty)
    for bad in ("<script", "onerror", "onclick", "javascript:", "<iframe"):
        assert bad not in clean


def test_audit_log_is_append_only(client, db):
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "auth.login"))
    with pytest.raises(Exception, match="append-only"):
        db.execute(text("UPDATE audit_logs SET action='x' WHERE id=:i"), {"i": entry.id})
    db.rollback()
    with pytest.raises(Exception, match="730 days"):
        db.execute(text("DELETE FROM audit_logs WHERE id=:i"), {"i": entry.id})
    db.rollback()


# ---- two-step verification ----

def _enable_mfa(client) -> tuple[str, list[str]]:
    setup = client.post("/api/auth/mfa/setup", json={"password": PASSWORD})
    assert setup.status_code == 200 and setup.json()["qr_svg"].startswith("<?xml")
    secret = setup.json()["secret"]
    r = client.post("/api/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


def _code(secret: str, steps_ahead: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * steps_ahead)


def test_mfa_sign_in_flow(app, client, db):
    secret, recovery = _enable_mfa(client)
    stored = db.scalar(select(User).where(User.email == "admin@trace.studio"))
    assert secret not in (stored.totp_secret_enc or "") and decrypt(stored.totp_secret_enc) == secret
    assert all(c not in str(stored.recovery_code_hashes) for c in recovery)

    anon = TestClient(app)
    r = anon.post("/api/auth/login", json={"email": "admin@trace.studio", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["mfa_required"] and "ts_session" not in r.cookies
    token = r.json()["mfa_token"]
    assert anon.get("/api/auth/me").status_code == 401  # the password alone gives no session
    assert anon.post("/api/auth/login/mfa", json={"mfa_token": token, "code": "000000"}).status_code == 401
    assert anon.post("/api/auth/login/mfa", json={"mfa_token": token + "x", "code": _code(secret, 1)}).status_code == 401
    ok = anon.post("/api/auth/login/mfa", json={"mfa_token": token, "code": _code(secret, 1)})
    assert ok.status_code == 200 and ok.json()["user"]["mfa_enabled"]
    # the same code can't be used twice
    again = TestClient(app)
    t2 = again.post("/api/auth/login", json={"email": "admin@trace.studio", "password": PASSWORD}).json()["mfa_token"]
    assert again.post("/api/auth/login/mfa", json={"mfa_token": t2, "code": _code(secret, 1)}).status_code == 401
    # a recovery code works once, and the admins are told
    assert again.post("/api/auth/login/mfa", json={"mfa_token": t2, "code": recovery[0]}).status_code == 200
    ratelimit.reset()  # 5 code attempts a minute per user; this test has used them up
    third = TestClient(app)
    t3 = third.post("/api/auth/login", json={"email": "admin@trace.studio", "password": PASSWORD}).json()["mfa_token"]
    assert third.post("/api/auth/login/mfa", json={"mfa_token": t3, "code": recovery[0]}).status_code == 401
    assert db.scalar(select(Notification).where(Notification.title.startswith("Recovery code used")))


def test_mfa_enrolment_needs_password_and_a_valid_code(client):
    assert client.post("/api/auth/mfa/setup", json={"password": "wrong"}).status_code == 400
    client.post("/api/auth/mfa/setup", json={"password": PASSWORD})
    assert client.post("/api/auth/mfa/enable", json={"code": "123456"}).status_code == 400
    assert client.get("/api/auth/me").json()["user"]["mfa_enabled"] is False


def test_mfa_disable_needs_password_and_code_and_alerts(client, db):
    secret, _ = _enable_mfa(client)
    assert client.post("/api/auth/mfa/disable", json={"password": PASSWORD, "code": "000000"}).status_code == 400
    assert client.post("/api/auth/mfa/disable", json={"password": "nope", "code": _code(secret, 1)}).status_code == 400
    assert client.post("/api/auth/mfa/disable", json={"password": PASSWORD, "code": _code(secret, 1)}).status_code == 200
    assert db.scalar(select(Notification).where(Notification.title.startswith("Two-step verification turned off")))


def test_admin_features_need_mfa_when_required(app, client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "require_admin_mfa", True)
    assert client.get("/api/auth/me").json()["mfa_setup_required"] is True
    r = client.get("/api/auth/users")
    assert r.status_code == 403 and "two-step" in r.json()["detail"]
    assert client.get("/api/contacts").status_code == 200  # everyday features keep working
    _enable_mfa(client)
    assert client.get("/api/auth/users").status_code == 200


def test_admin_can_reset_a_teammates_mfa(app, client, db):
    make_user(email="mate@trace.studio", role="member")
    mate = login(app, "mate@trace.studio")
    _enable_mfa(mate)
    mate_id = db.scalar(select(User.id).where(User.email == "mate@trace.studio"))
    assert client.post(f"/api/auth/users/{mate_id}/mfa/reset").json()["mfa_enabled"] is False
    assert mate.get("/api/auth/me").status_code == 401  # their sessions ended


def test_security_log_lists_events_for_admins(client):
    rows = client.get("/api/auth/audit-log").json()
    assert any(r["action"] == "auth.login" and r["user_email"] == "admin@trace.studio" for r in rows)


def test_production_rejects_unsafe_configuration(monkeypatch):
    from app.core.config import Settings

    s = Settings(app_env="production", cookie_secure=False, public_url="http://crm.example.com",
                 database_url="postgresql+psycopg://postgres@localhost/outreach", require_admin_mfa=False,
                 secret_key="x" * 40, token_encryption_keys="k")
    problems = " ".join(s.validate_for_runtime())
    for expected in ("COOKIE_SECURE", "PUBLIC_URL", "DATABASE_URL", "REQUIRE_ADMIN_MFA"):
        assert expected in problems
    assert Settings(app_env="production").admin_mfa_required is True
    assert Settings(app_env="development").admin_mfa_required is False
