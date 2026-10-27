"""Encryption for secrets at rest (OAuth tokens) and HMAC tokens (unsubscribe links)."""

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings


class CryptoError(Exception):
    pass


def _fernet() -> MultiFernet:
    keys = [k.strip() for k in get_settings().token_encryption_keys.split(",") if k.strip()]
    if not keys:
        raise CryptoError("TOKEN_ENCRYPTION_KEYS is not configured")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CryptoError("Unable to decrypt secret (wrong key?)") from exc


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign(payload: str, purpose: str) -> str:
    key = get_settings().secret_key.encode()
    mac = hmac.new(key, f"{purpose}:{payload}".encode(), hashlib.sha256).digest()[:16]
    return f"{_b64(payload.encode())}.{_b64(mac)}"


def unsign(token: str, purpose: str) -> str | None:
    try:
        p, m = token.split(".", 1)
        payload = _unb64(p).decode()
    except Exception:
        return None
    expected = sign(payload, purpose).split(".", 1)[1]
    if not hmac.compare_digest(expected, m):
        return None
    return payload
