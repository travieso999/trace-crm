import html
import logging
import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, reader
from app.core.config import get_settings
from app.core.crypto import CryptoError, encrypt
from app.core.db import get_db
from app.core.logging import log
from app.core.security import load_session
from app.models import Campaign, EmailAccount, SentMessage, User, campaign_mailboxes
from app.models.base import utcnow
from app.providers import get_provider
from app.providers import smtp as smtp_provider
from app.providers.base import OutgoingMessage, ProviderError, ProviderUnavailable
from app.providers.mime import new_message_id
from app.providers.sandbox import SandboxProvider
from app.schemas.common import Message
from app.schemas.mail import EmailAccountOut, EmailAccountUpdate
from app.services import app_settings, google_oauth, inbound, sending
from app.services.normalize import InvalidEmail, normalize_email

logger = logging.getLogger("app.email_accounts")
router = APIRouter(prefix="/api/email-accounts", tags=["email accounts"])


class SandboxCreate(BaseModel):
    email: str = Field(max_length=320)
    display_name: str | None = Field(default=None, max_length=200)


class SmtpConnectIn(BaseModel):
    email: str = Field(max_length=320)
    display_name: str | None = Field(default=None, max_length=200)
    password: str = Field(min_length=1, max_length=500)
    username: str | None = Field(default=None, max_length=320)  # defaults to the email address
    preset: str = Field(default="custom", pattern="^(custom|" + "|".join(smtp_provider.PRESETS) + ")$")
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_security: str | None = Field(default=None, pattern="^(ssl|starttls)$")
    imap_host: str | None = Field(default=None, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)


class TestEmailIn(BaseModel):
    to_email: str = Field(max_length=320)


class SimulateIn(BaseModel):
    sent_message_id: uuid.UUID
    kind: str = Field(default="reply", pattern="^(reply|auto_reply|bounce)$")
    body: str = Field(default="", max_length=20000)


def _out(db: Session, accounts: list[EmailAccount]) -> list[EmailAccountOut]:
    ids = [a.id for a in accounts]
    totals = dict(db.execute(
        select(SentMessage.email_account_id, func.count()).where(SentMessage.email_account_id.in_(ids))
        .group_by(SentMessage.email_account_id)).all()) if ids else {}
    links: dict[uuid.UUID, list[dict]] = {}
    if ids:
        for mid, cid, name in db.execute(
            select(campaign_mailboxes.c.email_account_id, Campaign.id, Campaign.name)
            .join(Campaign, Campaign.id == campaign_mailboxes.c.campaign_id)
            .where(campaign_mailboxes.c.email_account_id.in_(ids), Campaign.deleted_at.is_(None))
        ):
            links.setdefault(mid, []).append({"id": str(cid), "name": name})
    now = utcnow()
    out = []
    for a in accounts:
        o = EmailAccountOut.model_validate(a)
        if inbound.current_error(a) is None:
            o.last_error = o.last_error_at = None
        o.sent_today = sending.sent_today(db, a, now)
        o.sent_total = totals.get(a.id, 0)
        o.campaigns = links.get(a.id, [])
        out.append(o)
    return out


def _get(db: Session, account_id: uuid.UUID) -> EmailAccount:
    a = db.get(EmailAccount, account_id)
    if a is None:
        raise HTTPException(404, "Email account not found")
    return a


@router.get("/providers")
def providers(_: User = Depends(reader)):
    s = get_settings()
    return {
        "gmail": {"available": s.google_oauth_configured, "configured": s.google_oauth_configured,
                  "reason": None if s.google_oauth_configured else
                  "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and TOKEN_ENCRYPTION_KEYS on the server",
                  "redirect_uri": s.google_oauth_redirect_uri, "scopes": list(google_oauth.SCOPES)},
        "microsoft": {"available": False, "configured": bool(s.microsoft_client_id and s.microsoft_client_secret),
                      "reason": "Microsoft 365 OAuth connection is not enabled yet"},
        "smtp": {"available": bool(s.token_encryption_keys), "configured": bool(s.token_encryption_keys),
                 "reason": None if s.token_encryption_keys else "Set TOKEN_ENCRYPTION_KEYS on the server",
                 "presets": smtp_provider.PRESETS},
        "sandbox": {"available": s.sandbox_enabled, "configured": True,
                    "reason": None if s.sandbox_enabled else "Disabled in production",
                    "outbox_dir": s.sandbox_outbox_dir if s.sandbox_enabled else None},
        "real_sending": s.real_sending,
    }


# --- Google OAuth ---------------------------------------------------------------------------------

class OAuthStartIn(BaseModel):
    reconnect_account_id: uuid.UUID | None = None


@router.post("/oauth/google/start")
def google_start(body: OAuthStartIn, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Returns the Google consent URL. A POST (CSRF-checked) so authorization only starts from an explicit click."""
    hint = None
    if body.reconnect_account_id:
        acct = _get(db, body.reconnect_account_id)
        if acct.provider != "gmail":
            raise HTTPException(422, "Only Gmail mailboxes can be reconnected with Google")
        hint = acct.email
    try:
        url = google_oauth.start_authorization(db, user.id, body.reconnect_account_id, login_hint=hint)
    except google_oauth.OAuthFlowError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, user, "mailbox.oauth_started", "email_account", body.reconnect_account_id, request, provider="gmail")
    db.commit()
    return {"authorization_url": url}


def _back(**params: str) -> RedirectResponse:
    # Relative: the browser resolves it against the origin it used (the Next proxy), keeping cookies same-origin.
    return RedirectResponse(f"/email-accounts?{urlencode(params)}", status_code=303)


@router.get("/oauth/google/callback", include_in_schema=False)
def google_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    sess = load_session(db, request.cookies.get(get_settings().session_cookie_name))
    user = sess.user if sess else None
    try:
        if user is None:
            # still burn the state so it can't be replayed later
            if state:
                try:
                    google_oauth.consume_state(db, state, None)
                except google_oauth.OAuthFlowError:
                    pass
                db.commit()
            raise google_oauth.OAuthFlowError("not_signed_in")
        st = google_oauth.consume_state(db, state, user.id)
        db.commit()  # state is single-use even if the rest fails
        if user.role != "admin":
            raise google_oauth.OAuthFlowError("forbidden")
        if error:
            raise google_oauth.OAuthFlowError("access_denied" if error == "access_denied" else "google_error", error)
        if not code:
            raise google_oauth.OAuthFlowError("missing_code")
        try:
            tokens = google_oauth.exchange_code(code, st.code_verifier)
        except ProviderError as exc:
            raise google_oauth.OAuthFlowError("token_exchange_failed", str(exc)) from exc
        missing = set(google_oauth.SCOPES) - tokens.scopes
        if missing:
            raise google_oauth.OAuthFlowError("missing_scopes", ", ".join(sorted(missing)))
        profile = google_oauth.gmail_profile(tokens.access_token)
        email_addr = (profile.get("emailAddress") or "").lower()
        if not email_addr:
            raise google_oauth.OAuthFlowError("profile_failed")
        account = db.scalar(select(EmailAccount).where(EmailAccount.provider == "gmail", EmailAccount.email == email_addr))
        if st.reconnect_account_id and (account is None or account.id != st.reconnect_account_id):
            raise google_oauth.OAuthFlowError("wrong_account", email_addr)
        if not tokens.refresh_token and not (account and account.refresh_token_enc):
            raise google_oauth.OAuthFlowError("no_refresh_token")
        created = account is None
        if created:
            d = app_settings.get_section(db, "defaults")
            account = EmailAccount(provider="gmail", email=email_addr,
                                   daily_limit=d.get("daily_limit_per_mailbox", 30),
                                   min_interval_seconds=d.get("min_interval_seconds", 120),
                                   schedule=dict(d.get("schedule") or {}))
            db.add(account)
        google_oauth.store_tokens(account, tokens)
        account.status = "connected"
        account.consecutive_failures, account.circuit_open_until = 0, None
        account.last_error = account.last_error_at = None
        if created or account.sync_cursor is None:
            # replies are synced from this moment on; older mail is never imported
            account.sync_cursor = str(profile.get("historyId")) if profile.get("historyId") else None
        db.flush()
        audit(db, user, "mailbox.connected" if created else "mailbox.reconnected", "email_account", account.id,
              request, provider="gmail", email=email_addr)
        db.commit()
        return _back(connected="gmail", email=email_addr)
    except google_oauth.OAuthFlowError as exc:
        db.rollback()
        log(logger, logging.WARNING, "google_oauth_failed", code=exc.code)
        return _back(oauth_error=exc.code, **({"detail": str(exc)[:200]} if str(exc) != exc.code else {}))


@router.get("", response_model=list[EmailAccountOut])
def list_accounts(include_disconnected: bool = False, _: User = Depends(reader), db: Session = Depends(get_db)):
    stmt = select(EmailAccount).order_by(EmailAccount.email)
    if not include_disconnected:
        stmt = stmt.where(EmailAccount.status != "disconnected")
    return _out(db, list(db.scalars(stmt)))


@router.post("/sandbox", response_model=EmailAccountOut, status_code=201)
def create_sandbox(body: SandboxCreate, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    if not get_settings().sandbox_enabled:
        raise HTTPException(403, "Sandbox mailboxes are disabled in production")
    try:
        email_addr = normalize_email(body.email)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from exc
    d = app_settings.get_section(db, "defaults")
    existing = db.scalar(select(EmailAccount).where(EmailAccount.provider == "sandbox", EmailAccount.email == email_addr))
    if existing and existing.status != "disconnected":
        raise HTTPException(409, "This sandbox mailbox already exists")
    account = existing or EmailAccount(provider="sandbox", email=email_addr)
    account.display_name = (body.display_name or "").strip() or None
    account.status = "connected"
    account.daily_limit = d.get("daily_limit_per_mailbox", 30)
    account.min_interval_seconds = d.get("min_interval_seconds", 120)
    account.schedule = dict(d.get("schedule") or account.schedule or {})
    account.consecutive_failures, account.circuit_open_until, account.last_error = 0, None, None
    if not existing:
        db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This mailbox already exists") from exc
    audit(db, user, "mailbox.created", "email_account", account.id, request, provider="sandbox")
    db.commit()
    return _out(db, [account])[0]


@router.post("/smtp", response_model=EmailAccountOut, status_code=201)
def connect_smtp(body: SmtpConnectIn, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Connect (or reconnect) a mailbox by SMTP + IMAP password, e.g. Hostinger email. Both logins are checked
    before anything is saved; the password is stored encrypted."""
    try:
        email_addr = normalize_email(body.email)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from exc
    server = dict(smtp_provider.PRESETS.get(body.preset, {}))
    for f in ("smtp_host", "smtp_port", "smtp_security", "imap_host", "imap_port"):
        if getattr(body, f) is not None:
            server[f] = getattr(body, f)
    missing = [f for f in ("smtp_host", "smtp_port", "imap_host", "imap_port") if not server.get(f)]
    if missing:
        raise HTTPException(422, "Missing server settings: " + ", ".join(missing))
    server.setdefault("smtp_security", "ssl")
    server["smtp_host"], server["imap_host"] = server["smtp_host"].strip().lower(), server["imap_host"].strip().lower()
    server["username"] = (body.username or "").strip() or email_addr
    try:
        smtp_provider.SmtpImapProvider(email_addr, smtp_provider.ServerConfig.from_account(server, body.password),
                                       send_block_reason=lambda _: None).verify()
    except ProviderError as exc:
        raise HTTPException(422, f"Could not sign in to the mail server: {exc}") from exc
    try:
        password_enc = encrypt(body.password)
    except CryptoError as exc:
        raise HTTPException(409, str(exc)) from exc

    d = app_settings.get_section(db, "defaults")
    existing = db.scalar(select(EmailAccount).where(EmailAccount.provider == "smtp", EmailAccount.email == email_addr))
    account = existing or EmailAccount(provider="smtp", email=email_addr)
    if not existing or existing.status == "disconnected":
        account.daily_limit = d.get("daily_limit_per_mailbox", 30)
        account.min_interval_seconds = d.get("min_interval_seconds", 120)
        account.schedule = dict(d.get("schedule") or account.schedule or {})
        account.sync_cursor = None  # replies are read from now on
    if body.display_name is not None or not existing:
        account.display_name = (body.display_name or "").strip() or None
    account.server, account.password_enc, account.status = server, password_enc, "connected"
    account.consecutive_failures, account.circuit_open_until = 0, None
    account.last_error = account.last_error_at = None
    if not existing:
        db.add(account)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "This mailbox already exists") from exc
    audit(db, user, "mailbox.reconnected" if existing else "mailbox.created", "email_account", account.id, request,
          provider="smtp", smtp_host=server["smtp_host"])
    db.commit()
    return _out(db, [account])[0]


@router.get("/{account_id}", response_model=EmailAccountOut)
def get_account(account_id: uuid.UUID, _: User = Depends(reader), db: Session = Depends(get_db)):
    return _out(db, [_get(db, account_id)])[0]


@router.patch("/{account_id}", response_model=EmailAccountOut)
def update_account(account_id: uuid.UUID, body: EmailAccountUpdate, request: Request, user: User = Depends(admin),
                   db: Session = Depends(get_db)):
    a = _get(db, account_id)
    data = body.model_dump(exclude_unset=True)
    for f in ("display_name", "daily_limit", "min_interval_seconds", "signature_html"):
        if f in data:
            setattr(a, f, data[f])
    if data.get("schedule"):
        a.schedule = {k: v for k, v in data["schedule"].items() if k != "use_recipient_timezone"}
    audit(db, user, "mailbox.updated", "email_account", a.id, request, fields=sorted(data))
    db.commit()
    return _out(db, [a])[0]


@router.post("/{account_id}/pause", response_model=EmailAccountOut)
def pause_account(account_id: uuid.UUID, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    a = _get(db, account_id)
    if a.status == "disconnected":
        raise HTTPException(409, "Mailbox is disconnected")
    a.status = "paused"
    audit(db, user, "mailbox.paused", "email_account", a.id, request)
    db.commit()
    return _out(db, [a])[0]


@router.post("/{account_id}/resume", response_model=EmailAccountOut)
def resume_account(account_id: uuid.UUID, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    a = _get(db, account_id)
    if a.status == "disconnected":
        raise HTTPException(409, "Reconnect this mailbox instead")
    if a.status == "auth_error":
        raise HTTPException(409, "This mailbox needs to be reconnected (authorization expired)")
    a.status, a.consecutive_failures, a.circuit_open_until = "connected", 0, None
    audit(db, user, "mailbox.resumed", "email_account", a.id, request)
    db.commit()
    return _out(db, [a])[0]


@router.post("/{account_id}/sync")
def sync_now(account_id: uuid.UUID, _: User = Depends(admin), db: Session = Depends(get_db)):
    _get(db, account_id)
    db.close()
    return inbound.sync_mailbox(account_id)


@router.get("/{account_id}/test-recipients")
def test_recipients(account_id: uuid.UUID, _: User = Depends(admin), db: Session = Depends(get_db)):
    a = _get(db, account_id)
    return {"recipients": sorted(sending.test_recipients(db, a)), "real_sending": get_settings().real_sending}


@router.post("/{account_id}/test-email", response_model=Message)
def send_test_email(account_id: uuid.UUID, body: TestEmailIn, request: Request, user: User = Depends(admin),
                    db: Session = Depends(get_db)):
    """Send one fixed test email from this mailbox to an allow-listed address. It uses the "test" purpose (the only
    one REAL_SENDING=test_only lets through) and never involves a campaign, lead or the sending engine."""
    a = _get(db, account_id)
    if a.status != "connected":
        raise HTTPException(409, "Only a connected mailbox can send a test email")
    try:
        to = normalize_email(body.to_email)
    except InvalidEmail as exc:
        raise HTTPException(422, str(exc)) from exc
    if to not in sending.test_recipients(db, a):
        raise HTTPException(403, sending.TEST_RECIPIENT_ERROR)
    text = (f"This is a test email from Trace CRM, sent from {a.email} by {user.email}.\n\n"
            "It only checks that this mailbox can send. No campaign or prospect was involved.")
    rfc_id = new_message_id(a.email)
    out = OutgoingMessage(from_email=a.email, from_name=a.display_name, to_email=to,
                          subject="[TEST] Trace CRM mailbox check", text=text,
                          html="".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n")), rfc_message_id=rfc_id)
    try:
        result = get_provider(a, "test").send(out)
    except ProviderUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(502, f"Send failed: {exc}") from exc
    db.add(SentMessage(email_account_id=a.id, sent_by_user_id=user.id, kind="test", to_email=to, from_email=a.email,
                       subject=out.subject, body_html=out.html, rfc_message_id=rfc_id,
                       provider_message_id=result.provider_message_id, provider_thread_id=result.provider_thread_id))
    audit(db, user, "mailbox.test_email", "email_account", a.id, request, to=to)
    db.commit()
    where = " (sandbox: written to the local outbox)" if a.provider == "sandbox" else ""
    return Message(message=f"Test email sent to {to}{where}")


@router.delete("/{account_id}", response_model=Message)
def disconnect(account_id: uuid.UUID, request: Request, user: User = Depends(admin), db: Session = Depends(get_db)):
    """Disconnect (history is kept). The mailbox is removed from campaigns; leads on it move on their next step 1."""
    a = _get(db, account_id)
    warning = google_oauth.revoke(a) if a.provider == "gmail" else None
    a.status = "disconnected"
    a.access_token_enc = a.refresh_token_enc = a.password_enc = None
    a.token_expires_at, a.sync_cursor = None, None
    db.execute(campaign_mailboxes.delete().where(campaign_mailboxes.c.email_account_id == a.id))
    audit(db, user, "mailbox.disconnected", "email_account", a.id, request, revoke_warning=warning)
    db.commit()
    return Message(message="Mailbox disconnected" + (f". {warning}" if warning else
                                                      " and access revoked at Google" if a.provider == "gmail" else ""))


@router.post("/{account_id}/sandbox/simulate")
def simulate_inbound(account_id: uuid.UUID, body: SimulateIn, request: Request, user: User = Depends(admin),
                     db: Session = Depends(get_db)):
    """Dev only: drop a reply / auto-reply / bounce for a sent message into the sandbox inbox, then sync."""
    a = _get(db, account_id)
    if a.provider != "sandbox" or not get_settings().sandbox_enabled:
        raise HTTPException(403, "Only sandbox mailboxes can receive simulated mail")
    sent = db.get(SentMessage, body.sent_message_id)
    if sent is None or sent.email_account_id != a.id:
        raise HTTPException(404, "Sent message not found for this mailbox")
    raw = inbound.build_simulated_reply(sent, body.body or "Thanks for reaching out.", kind=body.kind)
    SandboxProvider(a.email).deliver_inbound(raw, sent.provider_thread_id)
    audit(db, user, "mailbox.simulated_inbound", "email_account", a.id, request, kind=body.kind)
    db.commit()
    db.close()
    return inbound.sync_mailbox(account_id)
