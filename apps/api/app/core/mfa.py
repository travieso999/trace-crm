"""Two-step verification: TOTP (RFC 6238, any authenticator app) or a code emailed at each sign-in, plus
single-use recovery codes.

Emailed codes: 6 random digits, valid for EMAIL_CODE_MINUTES and once only. Only an HMAC of the code (keyed with
SECRET_KEY) is stored, so the database alone can't reveal or brute-force it. Guessing is bounded by the same
per-user rate limit and account lockout as authenticator codes. They are sent with the NOTIFY_SMTP_* settings, so
the email method is offered only when those are configured.

The TOTP secret is Fernet-encrypted at rest like the mailbox tokens. Each accepted code's time step is stored, so a
code can't be replayed. Recovery codes are stored only as SHA-256 hashes (they are long random strings, so a slow
hash adds nothing) and each works once.

Signing in with two-step verification on is two requests: the password step returns a short-lived signed
challenge instead of a session, and the code step exchanges it for one.
"""

import hashlib
import hmac
import io
import secrets
import time
from datetime import timedelta

import pyotp
import segno

from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt, sign, unsign
from app.models import User
from app.models.base import utcnow
from app.services import notify_email

ISSUER = "Trace CRM"
CHALLENGE_TTL_SECONDS = 300
RECOVERY_CODES = 10
EMAIL_CODE_MINUTES = 10


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning(user: User, secret: str) -> dict:
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    buf = io.BytesIO()
    segno.make(uri, error="m").save(buf, kind="svg", scale=5, border=2, dark="#0f0f0d", light="#ffffff")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": buf.getvalue().decode()}


def _step_of(code: str, secret: str, at: float) -> int | None:
    """The time step `code` is valid for (current one, or one either side for clock drift), else None."""
    totp = pyotp.TOTP(secret)
    now_step = int(at // totp.interval)
    for step in (now_step, now_step - 1, now_step + 1):
        if hmac.compare_digest(totp.at(step * totp.interval), code):
            return step
    return None


def verify_totp(user: User, code: str, secret: str | None = None) -> bool:
    """Checks a 6-digit code and burns its time step. `secret` is passed while enrolling (not yet enabled)."""
    code = "".join(code.split())
    if not (len(code) == 6 and code.isdigit()):
        return False
    secret = secret or decrypt(user.totp_secret_enc)
    if not secret:
        return False
    step = _step_of(code, secret, time.time())
    if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
        return False
    user.totp_last_step = step
    return True


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").strip().lower().encode()).hexdigest()


def new_recovery_codes(user: User) -> list[str]:
    codes = [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(RECOVERY_CODES)]
    user.recovery_code_hashes = [_hash_recovery(c) for c in codes]
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    h = _hash_recovery(code)
    remaining = list(user.recovery_code_hashes or [])
    match = next((x for x in remaining if hmac.compare_digest(x, h)), None)
    if match is None:
        return False
    remaining.remove(match)
    user.recovery_code_hashes = remaining
    return True


# ---- emailed codes ----

def email_codes_available() -> bool:
    return get_settings().notify_email_configured


def mask_email(email: str) -> str:
    """el•••@gmail.com: enough to recognise the address on the sign-in screen without revealing it."""
    local, _, domain = email.partition("@")
    return f"{local[:2]}•••@{domain}" if domain else "•••"


def _hash_email_code(user: User, code: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"{user.id}:{code}".encode(), hashlib.sha256).hexdigest()


def send_email_code(user: User) -> None:
    """Mails a new code to the account address (replacing any earlier one). Raises NotifyEmailError."""
    code = f"{secrets.randbelow(10**6):06d}"
    user.email_code_hash = _hash_email_code(user, code)
    user.email_code_expires_at = utcnow() + timedelta(minutes=EMAIL_CODE_MINUTES)
    notify_email.send(
        user.email, f"{code} is your {ISSUER} sign-in code",
        f"Your {ISSUER} sign-in code is:\n\n    {code}\n\n"
        f"It works once and expires in {EMAIL_CODE_MINUTES} minutes.\n\n"
        "If you didn't just try to sign in, someone has your password: change it in Settings -> Security.")


def verify_email_code(user: User, code: str) -> bool:
    """Checks the pending emailed code and uses it up."""
    code = "".join(code.split())
    if not (len(code) == 6 and code.isdigit()) or not user.email_code_hash or not user.email_code_expires_at:
        return False
    if user.email_code_expires_at < utcnow():
        return False
    if not hmac.compare_digest(user.email_code_hash, _hash_email_code(user, code)):
        return False
    user.email_code_hash, user.email_code_expires_at = None, None
    return True


def verify_current_factor(user: User, code: str) -> bool:
    """The user's own method (authenticator or emailed code), not a recovery code."""
    return verify_email_code(user, code) if user.mfa_method == "email" else verify_totp(user, code)


def verify_second_factor(user: User, code: str) -> str | None:
    """'totp', 'email' or 'recovery' when `code` is a valid factor for this user, else None."""
    if verify_current_factor(user, code):
        return user.mfa_method
    if len("".join(code.split())) > 6 and use_recovery_code(user, code):
        return "recovery"
    return None


def enable(user: User, secret: str) -> list[str]:
    user.mfa_method = "totp"
    user.totp_secret_enc = encrypt(secret)
    user.totp_enabled_at = utcnow()
    return new_recovery_codes(user)


def enable_email(user: User) -> list[str]:
    user.mfa_method = "email"
    user.totp_secret_enc = None
    user.totp_last_step = None
    user.totp_enabled_at = utcnow()
    return new_recovery_codes(user)


def disable(user: User) -> None:
    user.totp_secret_enc = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    user.recovery_code_hashes = []
    user.mfa_method = "totp"
    user.email_code_hash = None
    user.email_code_expires_at = None


def _fingerprint(user: User) -> str:
    # A challenge stops working if the password or two-step setup changes after it was issued
    return hashlib.sha256(f"{user.password_hash}|{user.totp_secret_enc}|{user.mfa_method}".encode()).hexdigest()[:16]


def issue_challenge(user: User) -> str:
    return sign(f"{user.id}|{int(time.time()) + CHALLENGE_TTL_SECONDS}|{_fingerprint(user)}", "mfa-login")


def challenge_user_id(token: str) -> str | None:
    payload = unsign(token, "mfa-login")
    if not payload:
        return None
    try:
        user_id, expires, _ = payload.split("|", 2)
        if int(expires) < time.time():
            return None
    except ValueError:
        return None
    return user_id


def challenge_matches(token: str, user: User) -> bool:
    payload = unsign(token, "mfa-login") or ""
    return payload.rsplit("|", 1)[-1] == _fingerprint(user)


def required_for(user: User) -> bool:
    return user.role == "admin" and get_settings().admin_mfa_required
