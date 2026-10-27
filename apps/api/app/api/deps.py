import secrets
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core import mfa, ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import load_session
from app.models import AuditLog, User, UserSession
from app.services import security_alerts

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}
DENIALS_BEFORE_ALERT = 10


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd and get_settings().trust_proxy_headers:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def current_session(request: Request, db: Session = Depends(get_db)) -> UserSession:
    token = request.cookies.get(get_settings().session_cookie_name)
    sess = load_session(db, token)
    if sess is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method not in SAFE_METHODS:
        header = request.headers.get("x-csrf-token")
        if not header or not secrets.compare_digest(header, sess.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return sess


def current_user(sess: UserSession = Depends(current_session)) -> User:
    return sess.user


def require_role(min_role: str) -> Callable[[User], User]:
    def dep(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
            _denied(db, user, request, min_role)
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        if min_role == "admin" and mfa.required_for(user) and not user.mfa_enabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Turn on two-step verification (Settings → Security) to use admin features")
        return user

    return dep


def _denied(db: Session, user: User, request: Request, needed: str) -> None:
    """Permission errors are recorded; a burst of them (probing for data) alerts the admins."""
    audit(db, user, "authz.denied", None, None, request, method=request.method, path=request.url.path, needed=needed)
    if not ratelimit.hit(f"denied:{user.id}", DENIALS_BEFORE_ALERT, 600):
        security_alerts.notify_admins(
            db, f"Repeated permission errors: {user.email}",
            f"{user.email} ({user.role}) was refused access {DENIALS_BEFORE_ALERT}+ times in 10 minutes, "
            f"most recently {request.method} {request.url.path}.", throttle_key=f"denied:{user.id}")
    db.commit()


# Viewers can read; members can write; admins manage users/settings/mailboxes.
reader = require_role("viewer")
writer = require_role("member")
admin = require_role("admin")


def audit(db: Session, user: User | None, action: str, entity_type: str | None = None, entity_id=None,
          request: Request | None = None, **data) -> None:
    db.add(
        AuditLog(
            user_id=user.id if user else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            ip_address=client_ip(request) if request else None,
            data=data,
        )
    )
