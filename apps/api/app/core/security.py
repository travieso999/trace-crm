import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User, UserSession
from app.models.base import utcnow

# argon2-cffi defaults to Argon2id (RFC 9106 low-memory profile)
_hasher = PasswordHasher()
SEEN_RESOLUTION = timedelta(minutes=1)
# Pre-computed hash used to equalise timing when the user does not exist
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    sess = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        ip_address=ip,
        user_agent=(user_agent or "")[:400],
        expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
    )
    db.add(sess)
    db.flush()
    return token, sess


def load_session(db: Session, token: str | None) -> UserSession | None:
    if not token:
        return None
    sess = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    now = utcnow()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= now:
        return None
    if not sess.user.is_active:
        return None
    seen = sess.last_seen_at or sess.created_at
    if seen <= now - timedelta(minutes=get_settings().session_idle_minutes):
        return None
    if seen <= now - SEEN_RESOLUTION:  # at most one write a minute per session
        db.execute(update(UserSession).where(UserSession.id == sess.id).values(last_seen_at=now))
        db.commit()
        sess.last_seen_at = now
    return sess


def validate_password_strength(password: str) -> None:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if password.lower() == password or password.upper() == password or not any(c.isdigit() for c in password):
        raise ValueError("Password must contain upper and lower case letters and a digit")
