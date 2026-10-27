"""Gmail OAuth + adapter tests. Google is replaced by an in-process fake (httpx.MockTransport): no network."""

import base64
import email
import json
from datetime import timedelta
from email import policy
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models import CampaignContact, Contact, EmailAccount, OAuthState, SentMessage
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.base import OutgoingMessage, ProviderAuthError, ProviderUnavailable
from app.services import google_oauth, sending
from tests.conftest import login, make_user

GMAIL = "sam@tracestudio.dev"
REDIRECT = "http://localhost:3000/api/email-accounts/oauth/google/callback"
ALWAYS = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59", "timezone": "UTC"}


class FakeGoogle:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.sent: list[dict] = []
        self.scope = " ".join(google_oauth.SCOPES)
        self.refresh_ok = True
        self.give_refresh_token = True
        self.history_id = 1000
        self.inbox: list[dict] = []  # {id, threadId, raw, labelIds}
        self.send_status = 200
        self.n = 0

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        url, path = req.url, req.url.path
        if str(url).startswith(google_oauth.TOKEN_URL):
            form = parse_qs(req.content.decode())
            grant = form["grant_type"][0]
            assert form["client_id"] == ["cid.apps.googleusercontent.com"] and form["client_secret"] == ["shh"]
            if grant == "authorization_code":
                assert form["redirect_uri"] == [REDIRECT] and form["code_verifier"][0]
                if form["code"] == ["bad"]:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad Request"})
                body = {"access_token": "at-1", "expires_in": 3599, "scope": self.scope, "token_type": "Bearer"}
                if self.give_refresh_token:
                    body["refresh_token"] = "rt-1"
                return httpx.Response(200, json=body)
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
            self.n += 1
            return httpx.Response(200, json={"access_token": f"at-refreshed-{self.n}", "expires_in": 3599, "scope": self.scope})
        if str(url).startswith(google_oauth.REVOKE_URL):
            return httpx.Response(200)
        assert req.headers["authorization"].startswith("Bearer at-")
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": GMAIL, "historyId": str(self.history_id)})
        if path.endswith("/messages/send"):
            if self.send_status != 200:
                return httpx.Response(self.send_status, json={"error": {"message": "nope", "errors": [{"reason": "rateLimitExceeded"}]}})
            body = json.loads(req.content)
            self.n += 1
            mid, tid = f"g-{self.n}", body.get("threadId") or f"thread-{self.n}"
            self.sent.append({"id": mid, "threadId": tid, "body": body})
            return httpx.Response(200, json={"id": mid, "threadId": tid, "labelIds": ["SENT"]})
        if path.endswith("/history"):
            start = int(url.params["startHistoryId"])
            if start < 500:
                return httpx.Response(404, json={"error": {"message": "Requested entity was not found."}})
            hist = [{"id": str(self.history_id), "messagesAdded": [{"message": {"id": m["id"], "threadId": m["threadId"],
                                                                                "labelIds": m["labelIds"]}}]}
                    for m in self.inbox]
            return httpx.Response(200, json={"history": hist, "historyId": str(self.history_id)})
        if "/messages/" in path:
            mid = path.rsplit("/", 1)[1]
            m = next(x for x in self.inbox if x["id"] == mid)
            return httpx.Response(200, json={"id": mid, "threadId": m["threadId"], "internalDate": str(int(utcnow().timestamp() * 1000)),
                                             "raw": base64.urlsafe_b64encode(m["raw"]).decode()})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"resultSizeEstimate": 0})
        return httpx.Response(404)

    def sent_mime(self, i=-1):
        raw = self.sent[i]["body"]["raw"]
        return email.message_from_bytes(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)), policy=policy.default)


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    s = get_settings()
    monkeypatch.setattr(s, "google_client_id", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(s, "google_client_secret", "shh")
    monkeypatch.setattr(s, "public_url", "http://localhost:3000")
    monkeypatch.setattr(s, "real_sending", "disabled")
    monkeypatch.setattr(google_oauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handle)))
    return fake


def connect(client, code="good", reconnect=None):
    r = client.post("/api/email-accounts/oauth/google/start", json={"reconnect_account_id": reconnect})
    assert r.status_code == 200, r.text
    url = urlparse(r.json()["authorization_url"])
    state = parse_qs(url.query)["state"][0]
    cb = client.c.get("/api/email-accounts/oauth/google/callback", params={"code": code, "state": state},
                      follow_redirects=False)
    assert cb.status_code == 303
    return cb.headers["location"], state


def test_start_builds_least_privilege_pkce_url(client, google, db):
    r = client.post("/api/email-accounts/oauth/google/start", json={})
    url = urlparse(r.json()["authorization_url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == google_oauth.AUTH_URL
    assert q["redirect_uri"] == REDIRECT
    assert q["scope"].split() == ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) == 43
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["response_type"] == "code"
    row = db.scalar(select(OAuthState))
    assert row.state == q["state"] and row.expires_at > utcnow() and row.code_verifier not in r.text
    assert google.requests == []  # starting never calls Google; the browser is redirected by the user's click
    providers = client.get("/api/email-accounts/providers").json()
    assert providers["gmail"]["available"] and providers["gmail"]["redirect_uri"] == REDIRECT
    assert providers["real_sending"] == "disabled"


def test_start_requires_config_admin_and_csrf(app, client, google, monkeypatch):
    make_user("member@trace.studio", role="member")
    assert login(app, "member@trace.studio").post("/api/email-accounts/oauth/google/start", json={}).status_code == 403
    assert client.c.post("/api/email-accounts/oauth/google/start", json={}).status_code == 403  # no CSRF header
    monkeypatch.setattr(get_settings(), "google_client_secret", None)
    r = client.post("/api/email-accounts/oauth/google/start", json={})
    assert r.status_code == 409 and "not configured" in r.text


def test_callback_connects_and_encrypts_tokens(client, google, db):
    location, state = connect(client)
    assert location == f"/email-accounts?connected=gmail&email={GMAIL.replace('@', '%40')}"
    acct = db.scalar(select(EmailAccount).where(EmailAccount.provider == "gmail"))
    assert acct.email == GMAIL and acct.status == "connected"
    assert acct.access_token_enc and "at-1" not in acct.access_token_enc and decrypt(acct.access_token_enc) == "at-1"
    assert decrypt(acct.refresh_token_enc) == "rt-1"
    assert set(acct.scopes.split()) == set(google_oauth.SCOPES)
    assert acct.sync_cursor == "1000" and acct.token_expires_at > utcnow()
    # tokens never leave the API
    listed = client.get("/api/email-accounts").text
    assert "at-1" not in listed and "rt-1" not in listed and "token_enc" not in listed
    # state is single use
    replay = client.c.get("/api/email-accounts/oauth/google/callback", params={"code": "good", "state": state},
                          follow_redirects=False)
    assert "oauth_error=state_invalid" in replay.headers["location"]


@pytest.mark.parametrize("scenario,expected", [
    ("denied", "access_denied"), ("bad_state", "state_invalid"), ("expired", "state_expired"),
    ("missing_scope", "missing_scopes"), ("bad_code", "token_exchange_failed"), ("no_refresh", "no_refresh_token"),
])
def test_callback_errors_are_explained(client, google, db, scenario, expected):
    r = client.post("/api/email-accounts/oauth/google/start", json={})
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    params = {"code": "good", "state": state}
    if scenario == "denied":
        params = {"error": "access_denied", "state": state}
    elif scenario == "bad_state":
        params["state"] = "forged"
    elif scenario == "expired":
        db.execute(OAuthState.__table__.update().values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    elif scenario == "missing_scope":
        google.scope = google_oauth.SCOPES[0]  # user unticked "read" on the consent screen
    elif scenario == "bad_code":
        params["code"] = "bad"
    elif scenario == "no_refresh":
        google.give_refresh_token = False
    cb = client.c.get("/api/email-accounts/oauth/google/callback", params=params, follow_redirects=False)
    assert cb.status_code == 303 and f"oauth_error={expected}" in cb.headers["location"]
    assert db.scalar(select(EmailAccount).where(EmailAccount.provider == "gmail")) is None


def test_callback_requires_the_same_signed_in_admin(app, client, google):
    r = client.post("/api/email-accounts/oauth/google/start", json={})
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    from fastapi.testclient import TestClient

    anon = TestClient(app).get("/api/email-accounts/oauth/google/callback", params={"code": "good", "state": state},
                              follow_redirects=False)
    assert "oauth_error=not_signed_in" in anon.headers["location"]
    # the state was burned by the failed attempt
    cb = client.c.get("/api/email-accounts/oauth/google/callback", params={"code": "good", "state": state},
                      follow_redirects=False)
    assert "oauth_error=state_invalid" in cb.headers["location"]
    make_user("other@trace.studio")
    other = login(app, "other@trace.studio")
    r = client.post("/api/email-accounts/oauth/google/start", json={})
    state = parse_qs(urlparse(r.json()["authorization_url"]).query)["state"][0]
    cb = other.c.get("/api/email-accounts/oauth/google/callback", params={"code": "good", "state": state},
                     follow_redirects=False)
    assert "oauth_error=session_mismatch" in cb.headers["location"]


def test_refresh_persists_and_revocation_marks_auth_error(client, google, db):
    connect(client)
    acct = db.scalar(select(EmailAccount))
    db.execute(EmailAccount.__table__.update().values(token_expires_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert google_oauth.access_token(acct.id) == "at-refreshed-1"
    db.expire_all()
    assert decrypt(db.get(EmailAccount, acct.id).access_token_enc) == "at-refreshed-1"
    assert google_oauth.access_token(acct.id) == "at-refreshed-1"  # still fresh: no second refresh

    google.refresh_ok = False
    with pytest.raises(ProviderAuthError):
        google_oauth.access_token(acct.id, force_refresh=True)
    db.expire_all()
    acct = db.get(EmailAccount, acct.id)
    assert acct.status == "auth_error" and "reconnect" in acct.last_error
    # reconnect restores it
    google.refresh_ok = True
    location, _ = connect(client, reconnect=str(acct.id))
    assert "connected=gmail" in location
    db.expire_all()
    assert db.get(EmailAccount, acct.id).status == "connected"


def test_disconnect_revokes_and_clears_tokens(client, google, db):
    connect(client)
    acct = db.scalar(select(EmailAccount))
    r = client.delete(f"/api/email-accounts/{acct.id}")
    assert r.status_code == 200 and "revoked" in r.json()["message"]
    revoke = [q for q in google.requests if str(q.url).startswith(google_oauth.REVOKE_URL)]
    assert len(revoke) == 1 and parse_qs(revoke[0].content.decode())["token"] == ["rt-1"]
    db.expire_all()
    acct = db.get(EmailAccount, acct.id)
    assert acct.status == "disconnected" and acct.access_token_enc is None and acct.refresh_token_enc is None


def _campaign(client, db, mailbox_id):
    client.put("/api/settings/compliance", json={"physical_address": "123 Main St, Suite 4, Austin, TX 78701"})
    client.patch(f"/api/email-accounts/{mailbox_id}", json={"schedule": ALWAYS})
    db.execute(EmailAccount.__table__.update().values(min_interval_seconds=0))
    db.commit()
    contact = client.post("/api/contacts", json={"email": "owner@acmebail.com", "first_name": "Ann"}).json()
    c = client.post("/api/campaigns", json={"name": "Gmail test", "mailbox_ids": [mailbox_id],
                                            "schedule": {**ALWAYS, "use_recipient_timezone": False}}).json()
    client.put(f"/api/campaigns/{c['id']}/steps", json={"steps": [
        {"subject": "Hello {{first_name}}", "body": "Hi {{first_name}}"},
        {"delay_days": 1, "body": "Following up", "reply_in_thread": True}]})
    client.post(f"/api/campaigns/{c['id']}/contacts", json={"contact_ids": [contact["id"]]})
    return c["id"], contact["id"]


def test_sending_is_disabled_by_default(client, google, db, monkeypatch):
    connect(client)
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "admin@trace.studio")  # so the mode is what blocks
    mid = str(db.scalar(select(EmailAccount.id)))
    cid, _ = _campaign(client, db, mid)
    r = client.post(f"/api/campaigns/{cid}/start")
    assert r.status_code == 409
    assert any("REAL_SENDING=disabled" in p for p in r.json()["detail"]["problems"])
    t = client.post("/api/campaigns/test-send", json={"subject": "s", "body": "b", "email_account_id": mid,
                                                       "to_email": "admin@trace.studio"})
    assert t.status_code == 409 and "REAL_SENDING=disabled" in t.text
    with pytest.raises(ProviderUnavailable):  # defence in depth: the adapter itself refuses
        get_provider(db.get(EmailAccount, mid)).send(None)
    assert google.sent == []


def test_test_only_mode_allows_one_allowlisted_test_email(client, google, db, monkeypatch, compliance):
    connect(client)
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "qa@tracestudio.dev")
    mid = str(db.scalar(select(EmailAccount.id)))
    body = {"subject": "Hi {{first_name}}", "body": "Hello from {{company_name}}", "email_account_id": mid}
    assert client.post("/api/campaigns/test-send", json={**body, "to_email": "stranger@gmail.com"}).status_code == 403
    # app users are not implicitly allowed for a real mailbox: only ALLOWED_TEST_RECIPIENTS
    assert client.post("/api/campaigns/test-send", json={**body, "to_email": "admin@trace.studio"}).status_code == 403
    r = client.post("/api/campaigns/test-send", json={**body, "to_email": "QA@tracestudio.dev"})
    assert r.status_code == 200, r.text
    assert len(google.sent) == 1
    m = google.sent_mime()
    assert m["To"] == "qa@tracestudio.dev" and m["Subject"] == "[TEST] Hi Alex" and GMAIL in m["From"]
    # campaigns stay blocked in test-only mode
    cid, _ = _campaign(client, db, mid)
    assert client.post(f"/api/campaigns/{cid}/start").status_code == 409
    assert len(google.sent) == 1


def test_mailbox_test_email_only_to_allowlist_in_test_only_mode(client, app, google, db, monkeypatch):
    connect(client)
    s = get_settings()
    monkeypatch.setattr(s, "real_sending", "test_only")
    monkeypatch.setattr(s, "allowed_test_recipients", "qa@tracestudio.dev, ops@tracestudio.dev")
    mid = str(db.scalar(select(EmailAccount.id)))
    r = client.get(f"/api/email-accounts/{mid}/test-recipients").json()
    assert r == {"recipients": ["ops@tracestudio.dev", "qa@tracestudio.dev"], "real_sending": "test_only"}

    url = f"/api/email-accounts/{mid}/test-email"
    assert client.post(url, json={"to_email": "stranger@gmail.com"}).status_code == 403
    assert client.post(url, json={"to_email": "admin@trace.studio"}).status_code == 403  # app user, not allow-listed
    assert client.post(url, json={"to_email": "not an email"}).status_code == 422
    make_user("member@trace.studio", role="member")
    assert login(app, "member@trace.studio").post(url, json={"to_email": "qa@tracestudio.dev"}).status_code == 403
    assert google.sent == []

    r = client.post(url, json={"to_email": "qa@tracestudio.dev"})
    assert r.status_code == 200, r.text
    assert len(google.sent) == 1
    m = google.sent_mime()
    assert m["To"] == "qa@tracestudio.dev" and m["Subject"].startswith("[TEST]") and GMAIL in m["From"]
    sent = db.scalar(select(SentMessage))
    assert sent.kind == "test" and sent.campaign_id is None and sent.contact_id is None
    # nothing else can send: campaigns stay blocked and the engine has nothing to do
    cid, _ = _campaign(client, db, mid)
    assert client.post(f"/api/campaigns/{cid}/start").status_code == 409
    assert not sending.send_due().get("sent")
    assert len(google.sent) == 1


def test_mailbox_test_email_blocked_when_sending_disabled(client, google, db, monkeypatch):
    connect(client)
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "qa@tracestudio.dev")
    mid = str(db.scalar(select(EmailAccount.id)))
    r = client.post(f"/api/email-accounts/{mid}/test-email", json={"to_email": "qa@tracestudio.dev"})
    assert r.status_code == 409 and "REAL_SENDING=disabled" in r.text
    assert google.sent == [] and db.scalar(select(SentMessage)) is None


def test_successful_sync_clears_stale_sync_error(client, google, db):
    connect(client)
    acct = db.scalar(select(EmailAccount))
    acct.last_error, acct.last_error_at = "The gmail adapter is not configured in this deployment", utcnow()
    db.commit()
    assert client.post(f"/api/email-accounts/{acct.id}/sync").json() == {"processed": 0}
    db.expire_all()
    acct = db.get(EmailAccount, acct.id)
    assert acct.last_error is None and acct.last_error_at is None and acct.status == "connected"


def test_test_only_campaign_sends_from_gmail_only_to_allowlisted_leads(client, google, db, monkeypatch, compliance):
    connect(client)
    s = get_settings()
    monkeypatch.setattr(s, "real_sending", "test_only")
    monkeypatch.setattr(s, "allowed_test_recipients", "qa@tracestudio.dev, ops@tracestudio.dev")
    mid = str(db.scalar(select(EmailAccount.id)))
    client.patch(f"/api/email-accounts/{mid}", json={"schedule": ALWAYS})
    db.execute(EmailAccount.__table__.update().values(min_interval_seconds=0))
    db.commit()
    ids = [client.post("/api/contacts", json={"email": e, "first_name": n}).json()["id"]
           for e, n in (("QA@tracestudio.dev", "Quinn"), ("ops@tracestudio.dev", "Olive"))]
    c = client.post("/api/campaigns", json={"name": "Gmail dry run", "mailbox_ids": [mid],
                                            "schedule": {**ALWAYS, "use_recipient_timezone": False}}).json()
    client.put(f"/api/campaigns/{c['id']}/steps", json={"steps": [
        {"subject": "Hello {{first_name}}", "body": "Hi {{first_name}}"},
        {"delay_days": 1, "body": "Following up", "reply_in_thread": True}]})

    # a real prospect enrolled → the campaign cannot start in test-only mode
    stranger = client.post("/api/contacts", json={"email": "owner@acmebail.com", "first_name": "Ann"}).json()["id"]
    client.post(f"/api/campaigns/{c['id']}/contacts", json={"contact_ids": [*ids, stranger]})
    r = client.post(f"/api/campaigns/{c['id']}/start")
    assert r.status_code == 409 and any("1 enrolled prospect(s) are not in ALLOWED_TEST_RECIPIENTS" in p
                                        for p in r.json()["detail"]["problems"])
    db.execute(CampaignContact.__table__.delete().where(CampaignContact.contact_id == stranger))
    db.commit()

    # only allow-listed leads → it starts and sends from Gmail, follow-ups thread as usual
    assert client.post(f"/api/campaigns/{c['id']}/start").status_code == 200
    assert sending.send_due() == {"sent": 2}
    assert sorted(google.sent_mime(i)["To"] for i in range(2)) == ["ops@tracestudio.dev", "qa@tracestudio.dev"]

    # a prospect enrolled after the start is never sent to (deferred at reservation)
    client.post(f"/api/campaigns/{c['id']}/contacts", json={"contact_ids": [stranger]})
    db.execute(CampaignContact.__table__.update().values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert sending.send_due() == {"sent": 2, "deferred": 1}  # the two follow-ups; the prospect is held back
    assert all(google.sent_mime(i)["To"] != "owner@acmebail.com" for i in range(len(google.sent)))
    assert len(google.sent) == 4

    # the adapter itself refuses anyone outside the allowlist, whatever the caller
    acct = db.get(EmailAccount, mid)
    with pytest.raises(ProviderUnavailable, match="not in ALLOWED_TEST_RECIPIENTS"):
        get_provider(acct, "campaign").send(OutgoingMessage(from_email=GMAIL, from_name=None, to_email="owner@acmebail.com",
                                                            subject="s", text="t", html="<p>t</p>", rfc_message_id="<x@y>"))
    assert len(google.sent) == 4


def test_api_hides_error_superseded_by_a_later_sync(client, google, db):
    connect(client)
    acct = db.scalar(select(EmailAccount))
    err = "The gmail adapter is not configured in this deployment"
    # recorded by an outdated process, then later syncs succeeded without clearing it
    acct.last_error, acct.last_error_at, acct.last_synced_at = err, utcnow() - timedelta(hours=10), utcnow()
    db.commit()
    [out] = client.get("/api/email-accounts").json()
    assert out["last_error"] is None and out["last_error_at"] is None
    assert client.get("/api/dashboard").json()["mailbox_health"][0]["last_error"] is None
    # an error newer than the last successful sync is still shown
    acct.last_error_at = utcnow()
    acct.last_synced_at = utcnow() - timedelta(minutes=5)
    db.commit()
    assert client.get("/api/email-accounts").json()[0]["last_error"] == err
    # and an authorization problem always is, until reconnect
    acct.status, acct.last_error_at, acct.last_synced_at = "auth_error", utcnow() - timedelta(hours=1), utcnow()
    db.commit()
    assert client.get("/api/email-accounts").json()[0]["last_error"] == err


def test_enabled_mode_sends_threads_and_syncs_replies(client, google, db, monkeypatch):
    connect(client)
    monkeypatch.setattr(get_settings(), "real_sending", "enabled")
    mid = str(db.scalar(select(EmailAccount.id)))
    cid, contact_id = _campaign(client, db, mid)
    assert client.post(f"/api/campaigns/{cid}/start").status_code == 200
    assert sending.send_due() == {"sent": 1}
    first = google.sent[0]
    assert "threadId" not in first["body"]
    sent = db.scalar(select(SentMessage))
    assert sent.provider_message_id == first["id"] and sent.provider_thread_id == first["threadId"]

    # follow-up goes into the same Gmail thread
    db.execute(CampaignContact.__table__.update().values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert sending.send_due() == {"sent": 1}
    assert google.sent[1]["body"]["threadId"] == first["threadId"]
    assert google.sent_mime(1)["In-Reply-To"] == google.sent_mime(0)["Message-ID"]

    # a reply lands in the inbox; our own sent copy in history is ignored
    reply = email.message.EmailMessage()
    reply["From"], reply["To"], reply["Subject"] = "Ann <owner@acmebail.com>", GMAIL, "Re: Hello Ann"
    reply["Message-ID"], reply["In-Reply-To"] = "<r1@acmebail.com>", google.sent_mime(1)["Message-ID"]
    reply.set_content("Not interested, thanks.")
    google.history_id = 1005
    google.inbox = [{"id": "in-1", "threadId": first["threadId"], "raw": reply.as_bytes(), "labelIds": ["INBOX", "UNREAD"]},
                    {"id": google.sent[1]["id"], "threadId": first["threadId"], "raw": b"", "labelIds": ["SENT"]}]
    r = client.post(f"/api/email-accounts/{mid}/sync").json()
    assert r == {"processed": 1}
    db.expire_all()
    assert db.scalar(select(EmailAccount.sync_cursor)) == "1005"
    assert db.get(Contact, contact_id).lead_status == "not_interested"
    inbox = client.get("/api/inbox").json()
    assert inbox["items"][0]["intent"] == "negative"
    # manual reply through Gmail is allowed in enabled mode and stays in the thread
    r = client.post(f"/api/inbox/{inbox['items'][0]['id']}/reply", json={"body": "Understood, thanks!"})
    assert r.status_code == 200, r.text
    assert google.sent[-1]["body"]["threadId"] == first["threadId"]


def test_expired_history_cursor_resets_and_rate_limits_retry(client, google, db, monkeypatch):
    connect(client)
    acct = db.scalar(select(EmailAccount))
    db.execute(EmailAccount.__table__.update().values(sync_cursor="1"))  # older than Gmail keeps
    db.commit()
    assert client.post(f"/api/email-accounts/{acct.id}/sync").json() == {"processed": 0}
    db.expire_all()
    assert db.get(EmailAccount, acct.id).sync_cursor == "1000"

    monkeypatch.setattr(get_settings(), "real_sending", "enabled")
    cid, _ = _campaign(client, db, str(acct.id))
    client.post(f"/api/campaigns/{cid}/start")
    google.send_status = 429
    assert sending.send_due() == {"retry": 1}
    db.expire_all()
    assert db.get(EmailAccount, acct.id).consecutive_failures == 1
