"""Google OAuth 2.0 (authorization code + PKCE) for Gmail mailboxes, and access-token lifecycle.

Tokens are Fernet-encrypted at rest (app.core.crypto) and never returned by the API or logged.
Refreshes run under a row lock so concurrent workers don't race each other.
"""

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import CryptoError, decrypt, encrypt
from app.core.db import SessionLocal
from app.models import EmailAccount, OAuthState
from app.models.base import utcnow
from app.providers.base import ProviderAuthError, TemporaryProviderError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - public endpoint, not a credential
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_UPLOAD = "https://gmail.googleapis.com/upload/gmail/v1/users/me"

# Least privilege for "send campaign mail + read replies". No modify/delete, no contacts, no Drive.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
)
STATE_TTL = timedelta(minutes=10)
REFRESH_MARGIN = timedelta(minutes=2)


class OAuthFlowError(Exception):
    """Ends the callback with ``code`` shown to the user on the Email Accounts page."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def http_client() -> httpx.Client:
    """Single factory for every Google HTTP call (tests replace it with a mock transport)."""
    return httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0))


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def start_authorization(db, user_id: uuid.UUID, reconnect_account_id: uuid.UUID | None = None,
                        login_hint: str | None = None) -> str:
    s = get_settings()
    if not s.google_oauth_configured:
        raise OAuthFlowError("not_configured", "Google OAuth client credentials are not configured on the server")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state=state, provider="google", user_id=user_id, code_verifier=verifier,
                      reconnect_account_id=reconnect_account_id, expires_at=utcnow() + STATE_TTL))
    params = {
        "client_id": s.google_client_id,
        "redirect_uri": s.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # refresh token
        "prompt": "consent",  # always return a refresh token, even on reconnect
        "include_granted_scopes": "false",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urlencode(params)}"


def consume_state(db, state: str | None, user_id: uuid.UUID | None) -> OAuthState:
    """Single use: the row is deleted whether or not it is still valid."""
    if not state:
        raise OAuthFlowError("state_invalid")
    row = db.scalar(select(OAuthState).where(OAuthState.state == state, OAuthState.provider == "google").with_for_update())
    if row is None:
        raise OAuthFlowError("state_invalid")
    db.delete(row)
    db.flush()
    if row.expires_at <= utcnow():
        raise OAuthFlowError("state_expired")
    if user_id is None or row.user_id != user_id:
        raise OAuthFlowError("session_mismatch")
    return row


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None
    expires_in: int
    scopes: set[str]


def _token_request(data: dict) -> dict:
    s = get_settings()
    try:
        with http_client() as c:
            r = c.post(TOKEN_URL, data={**data, "client_id": s.google_client_id, "client_secret": s.google_client_secret})
    except httpx.HTTPError as exc:
        raise TemporaryProviderError(f"Could not reach Google ({type(exc).__name__})") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 500:
        raise TemporaryProviderError(f"Google token endpoint returned {r.status_code}")
    if r.status_code != 200:
        # error / error_description only: never echo request or token material
        raise ProviderAuthError(f"{body.get('error', r.status_code)}: {body.get('error_description', '')}".strip(": "))
    return body


def exchange_code(code: str, verifier: str) -> Tokens:
    body = _token_request({"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
                           "redirect_uri": get_settings().google_oauth_redirect_uri})
    return Tokens(body["access_token"], body.get("refresh_token"), int(body.get("expires_in", 3600)),
                  set((body.get("scope") or "").split()))


def gmail_profile(access_token: str) -> dict:
    """{emailAddress, historyId, ...}. Works with gmail.readonly; no extra identity scope needed."""
    with http_client() as c:
        r = c.get(f"{GMAIL_API}/profile", headers={"Authorization": f"Bearer {access_token}"})
    if r.status_code != 200:
        raise OAuthFlowError("profile_failed", f"Gmail profile request failed ({r.status_code})")
    return r.json()


def store_tokens(account: EmailAccount, tokens: Tokens) -> None:
    account.access_token_enc = encrypt(tokens.access_token)
    if tokens.refresh_token:
        account.refresh_token_enc = encrypt(tokens.refresh_token)
    account.token_expires_at = utcnow() + timedelta(seconds=tokens.expires_in)
    if tokens.scopes:
        account.scopes = " ".join(sorted(tokens.scopes))


def access_token(account_id: uuid.UUID, *, force_refresh: bool = False) -> str:
    """A valid access token for the mailbox, refreshing (and persisting) it when needed."""
    with SessionLocal() as db:
        account = db.scalar(select(EmailAccount).where(EmailAccount.id == account_id).with_for_update())
        if account is None or account.provider != "gmail":
            raise ProviderAuthError("Mailbox not found")
        if account.status == "disconnected":
            raise ProviderAuthError("Mailbox is disconnected")
        try:
            current = decrypt(account.access_token_enc)
            refresh = decrypt(account.refresh_token_enc)
        except CryptoError as exc:
            raise ProviderAuthError("Stored Google tokens can't be decrypted (TOKEN_ENCRYPTION_KEYS changed?)") from exc
        fresh = account.token_expires_at and account.token_expires_at - REFRESH_MARGIN > utcnow()
        if current and fresh and not force_refresh:
            return current
        if not refresh:
            _mark_auth_error(db, account, "No refresh token stored; reconnect this mailbox")
            raise ProviderAuthError("No refresh token stored; reconnect this mailbox")
        try:
            tokens = Tokens(**_refresh(refresh))
        except ProviderAuthError as exc:
            _mark_auth_error(db, account, f"Google rejected the refresh token ({exc}); reconnect this mailbox")
            raise
        store_tokens(account, tokens)
        db.commit()
        return tokens.access_token


def _refresh(refresh_token: str) -> dict:
    body = _token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
            "expires_in": int(body.get("expires_in", 3600)), "scopes": set((body.get("scope") or "").split())}


def _mark_auth_error(db, account: EmailAccount, message: str) -> None:
    account.status, account.last_error, account.last_error_at = "auth_error", message[:2000], utcnow()
    db.commit()


def revoke(account: EmailAccount) -> str | None:
    """Best-effort revocation at Google. Returns a warning message on failure (the disconnect still proceeds)."""
    token = None
    try:
        token = decrypt(account.refresh_token_enc) or decrypt(account.access_token_enc)
    except CryptoError:
        return "Stored token could not be decrypted, so it was not revoked at Google"
    if not token:
        return None
    try:
        with http_client() as c:
            r = c.post(REVOKE_URL, data={"token": token})
    except httpx.HTTPError:
        return "Could not reach Google to revoke access; remove it at myaccount.google.com/permissions"
    if r.status_code not in (200, 400):  # 400 = already invalid
        return f"Google returned {r.status_code} when revoking; remove access at myaccount.google.com/permissions"
    return None
