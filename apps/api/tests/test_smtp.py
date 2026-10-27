"""SMTP + IMAP mailboxes (Hostinger and friends). The mail server is an in-process fake: no network."""

import email
import imaplib
import re
import smtplib
from datetime import timedelta
from email import policy

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models import CampaignContact, Contact, EmailAccount, SentMessage
from app.models.base import utcnow
from app.providers import get_provider
from app.providers.base import AmbiguousSendError, OutgoingMessage, PermanentProviderError, TemporaryProviderError
from app.services import sending

MAILBOX = "hello@tracestudio.dev"
PASSWORD = "Mailbox-Pass-123"
ALWAYS = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "00:00", "end": "23:59", "timezone": "UTC"}


class FakeMailServer:
    def __init__(self):
        self.password = PASSWORD
        self.logins: list[tuple[str, str, int]] = []
        self.sent: list[dict] = []  # {"from", "to", "raw"}
        self.appended: list[tuple[str, bytes]] = []
        self.inbox: dict[int, bytes] = {1: b"Subject: old\r\n\r\nalready here"}
        self.uidvalidity = 77
        self.rcpt_code = 250
        self.data_error: Exception | None = None

    def sent_mime(self, i):
        return email.message_from_bytes(self.sent[i]["raw"], policy=policy.default)


@pytest.fixture
def server(monkeypatch):
    srv = FakeMailServer()

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.host, self.port, self.envelope = host, port, {}

        def login(self, user, password):
            if password != srv.password:
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Authentication failed")
            srv.logins.append(("smtp", self.host, self.port))

        def starttls(self, context=None):
            pass

        def mail(self, sender):
            self.envelope["from"] = sender
            return 250, b"OK"

        def rcpt(self, to):
            self.envelope["to"] = to
            return srv.rcpt_code, b"recipient status"

        def data(self, raw):
            if srv.data_error:
                raise srv.data_error
            srv.sent.append({**self.envelope, "raw": raw})
            return 250, b"queued"

        def quit(self):
            pass

    class FakeIMAP:
        def __init__(self, host, port, timeout=None, ssl_context=None):
            self.host, self.port = host, port

        def login(self, user, password):
            if password != srv.password:
                raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Authentication failed.")
            srv.logins.append(("imap", self.host, self.port))

        def select(self, mailbox, readonly=False):
            assert readonly, "sync must not change flags"
            return "OK", [str(len(srv.inbox)).encode()]

        def response(self, code):
            return code, [str(srv.uidvalidity).encode()]

        def status(self, mailbox, items):
            return "OK", [f"INBOX (UIDNEXT {max(srv.inbox, default=0) + 1})".encode()]

        def uid(self, command, *args):
            if command == "SEARCH":
                start = int(re.search(r"UID (\d+):\*", args[1]).group(1))
                found = [u for u in sorted(srv.inbox) if u >= start] or [max(srv.inbox)]  # "*" = highest UID
                return "OK", [" ".join(map(str, found)).encode()]
            uid = int(args[0])
            raw = srv.inbox[uid]
            return "OK", [(f'1 (UID {uid} INTERNALDATE "27-Oct-2026 16:31:12 +0000" BODY[] {{{len(raw)}}}'.encode(), raw),
                          b")"]

        def list(self):
            return "OK", [b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"']

        def append(self, folder, flags, date, raw):
            srv.appended.append((folder, raw))
            return "OK", [b"APPEND completed"]

        def logout(self):
            pass

    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return srv


def connect(client, password=PASSWORD, **kw):
    return client.post("/api/email-accounts/smtp", json={"email": MAILBOX, "display_name": "Trace Studio",
                                                         "password": password, "preset": "hostinger", **kw})


def _campaign(client, db, mailbox_id):
    client.put("/api/settings/compliance", json={"physical_address": "123 Main St, Suite 4, Austin, TX 78701"})
    client.patch(f"/api/email-accounts/{mailbox_id}", json={"schedule": ALWAYS})
    db.execute(EmailAccount.__table__.update().values(min_interval_seconds=0))
    db.commit()
    contact = client.post("/api/contacts", json={"email": "owner@acmebail.com", "first_name": "Ann"}).json()
    c = client.post("/api/campaigns", json={"name": "SMTP test", "mailbox_ids": [mailbox_id],
                                            "schedule": {**ALWAYS, "use_recipient_timezone": False}}).json()
    client.put(f"/api/campaigns/{c['id']}/steps", json={"steps": [
        {"subject": "Hello {{first_name}}", "body": "Hi {{first_name}}"},
        {"delay_days": 1, "body": "Following up", "reply_in_thread": True}]})
    client.post(f"/api/campaigns/{c['id']}/contacts", json={"contact_ids": [contact["id"]]})
    return c["id"], contact["id"]


def test_connect_hostinger_checks_both_logins_and_encrypts_password(client, server, db):
    r = connect(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["provider"] == "smtp" and body["status"] == "connected" and body["display_name"] == "Trace Studio"
    assert body["server"] == {"smtp_host": "smtp.hostinger.com", "smtp_port": 465, "smtp_security": "ssl",
                              "imap_host": "imap.hostinger.com", "imap_port": 993, "username": MAILBOX}
    assert PASSWORD not in r.text and "password" not in body
    assert server.logins == [("smtp", "smtp.hostinger.com", 465), ("imap", "imap.hostinger.com", 993)]
    acct = db.scalar(select(EmailAccount))
    assert acct.password_enc != PASSWORD and decrypt(acct.password_enc) == PASSWORD
    assert client.get("/api/email-accounts/providers").json()["smtp"]["presets"]["hostinger"]["imap_port"] == 993


def test_wrong_password_or_missing_hosts_saves_nothing(client, server, db):
    r = connect(client, password="wrong")
    assert r.status_code == 422 and "username or password" in r.json()["detail"]
    r = client.post("/api/email-accounts/smtp", json={"email": MAILBOX, "password": PASSWORD, "preset": "custom"})
    assert r.status_code == 422 and "smtp_host" in r.json()["detail"]
    assert db.scalar(select(func.count()).select_from(EmailAccount)) == 0


def test_campaign_sends_over_smtp_threads_and_syncs_replies(client, server, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "real_sending", "enabled")
    mid = connect(client).json()["id"]
    cid, contact_id = _campaign(client, db, mid)
    # the first sync only records where the inbox is now: older mail is never imported
    assert client.post(f"/api/email-accounts/{mid}/sync").json() == {"processed": 0}
    db.expire_all()
    assert db.scalar(select(EmailAccount.sync_cursor)) == "77:1"

    assert client.post(f"/api/campaigns/{cid}/start").status_code == 200
    assert sending.send_due() == {"sent": 1}
    assert server.sent[0]["from"] == MAILBOX and server.sent[0]["to"] == "owner@acmebail.com"
    sent = db.scalar(select(SentMessage))
    assert server.sent_mime(0)["Message-ID"] == sent.rfc_message_id == sent.provider_message_id
    assert server.appended[0][0] == '"INBOX.Sent"'  # copy shows in webmail's Sent folder

    db.execute(CampaignContact.__table__.update().values(next_send_at=utcnow() - timedelta(minutes=1)))
    db.commit()
    assert sending.send_due() == {"sent": 1}
    assert server.sent_mime(1)["In-Reply-To"] == server.sent_mime(0)["Message-ID"]

    reply = email.message.EmailMessage()
    reply["From"], reply["To"], reply["Subject"] = "Ann <owner@acmebail.com>", MAILBOX, "Re: Hello Ann"
    reply["Message-ID"], reply["In-Reply-To"] = "<r1@acmebail.com>", server.sent_mime(1)["Message-ID"]
    reply["References"] = f"{server.sent_mime(0)['Message-ID']} {server.sent_mime(1)['Message-ID']}"
    reply.set_content("Not interested, thanks.")
    server.inbox[2] = reply.as_bytes()
    server.inbox[3] = b"From: news@example.com\r\nSubject: Newsletter\r\n\r\nunrelated"
    assert client.post(f"/api/email-accounts/{mid}/sync").json() == {"processed": 1}
    assert client.post(f"/api/email-accounts/{mid}/sync").json() == {"processed": 0}  # nothing twice
    db.expire_all()
    assert db.scalar(select(EmailAccount.sync_cursor)) == "77:3"
    assert db.get(Contact, contact_id).lead_status == "not_interested"
    inbox = client.get("/api/inbox").json()["items"]
    assert len(inbox) == 1 and inbox[0]["intent"] == "negative"
    r = client.post(f"/api/inbox/{inbox[0]['id']}/reply", json={"body": "Understood, thanks!"})
    assert r.status_code == 200, r.text
    assert server.sent_mime(-1)["In-Reply-To"] == "<r1@acmebail.com>"


def test_changed_password_needs_reconnect_and_reconnect_keeps_settings(client, server, db):
    mid = connect(client).json()["id"]
    client.patch(f"/api/email-accounts/{mid}", json={"daily_limit": 12})
    server.password = "rotated-Pass-456"
    client.post(f"/api/email-accounts/{mid}/sync")
    acct = client.get(f"/api/email-accounts/{mid}").json()
    assert acct["status"] == "auth_error" and "username or password" in acct["last_error"]
    assert client.post(f"/api/email-accounts/{mid}/resume").status_code == 409

    r = client.post("/api/email-accounts/smtp", json={"email": MAILBOX, "password": "rotated-Pass-456",
                                                      "preset": "hostinger"})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == mid and r.json()["status"] == "connected" and r.json()["last_error"] is None
    assert r.json()["daily_limit"] == 12 and r.json()["display_name"] == "Trace Studio"


def test_disconnect_forgets_the_password(client, server, db):
    mid = connect(client).json()["id"]
    assert client.delete(f"/api/email-accounts/{mid}").status_code == 200
    db.expire_all()
    assert db.scalar(select(EmailAccount.password_enc)) is None


def test_test_email_respects_real_sending(client, server, db, monkeypatch):
    mid = connect(client).json()["id"]
    monkeypatch.setattr(get_settings(), "allowed_test_recipients", "qa@tracestudio.dev")
    monkeypatch.setattr(get_settings(), "real_sending", "disabled")
    assert client.post(f"/api/email-accounts/{mid}/test-email", json={"to_email": "qa@tracestudio.dev"}).status_code == 409
    monkeypatch.setattr(get_settings(), "real_sending", "test_only")
    r = client.post(f"/api/email-accounts/{mid}/test-email", json={"to_email": "qa@tracestudio.dev"})
    assert r.status_code == 200, r.text
    assert server.sent[0]["to"] == "qa@tracestudio.dev"


@pytest.mark.parametrize("setup,error", [
    (lambda s: setattr(s, "rcpt_code", 550), PermanentProviderError),  # mailbox does not exist: nothing sent
    (lambda s: setattr(s, "rcpt_code", 451), TemporaryProviderError),  # greylisted: retry later
    (lambda s: setattr(s, "data_error", TimeoutError()), AmbiguousSendError),  # message handed over, no answer
])
def test_smtp_errors_map_to_retry_semantics(client, server, db, monkeypatch, setup, error):
    monkeypatch.setattr(get_settings(), "real_sending", "enabled")
    connect(client)
    setup(server)
    provider = get_provider(db.scalar(select(EmailAccount)), "campaign")
    msg = OutgoingMessage(from_email=MAILBOX, from_name=None, to_email="x@example.com", subject="s", text="t",
                          html="<p>t</p>", rfc_message_id="<m1@tracestudio.dev>")
    with pytest.raises(error):
        provider.send(msg)
    assert server.sent == []
    # an ambiguous send counts as delivered on reconcile rather than risking a duplicate cold email
    assert provider.find_sent("<m1@tracestudio.dev>").provider_message_id == "<m1@tracestudio.dev>"
