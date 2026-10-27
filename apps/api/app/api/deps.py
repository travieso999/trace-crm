from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import load_session
from app.models import AuditLog, User, UserSession

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2}


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
        if not header or header != sess.csrf_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
    return sess


def current_user(sess: UserSession = Depends(current_session)) -> User:
    return sess.user


def require_role(min_role: str) -> Callable[[User], User]:
    def dep(user: User = Depends(current_user)) -> User:
        if ROLE_RANK.get(user.role, -1) < ROLE_RANK[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient permissions")
        return user

    return dep


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
