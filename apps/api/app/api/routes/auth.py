import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, client_ip, current_session, reader
from app.core import permissions, ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (
    create_session,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models import CrmTask, User, UserSession
from app.models.base import utcnow
from app.schemas.auth import (
    LoginIn,
    MeOut,
    PasswordChange,
    PasswordReset,
    TeamMemberOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.schemas.common import Message

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(s.session_cookie_name, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    # Readable by JS so the SPA can echo it in X-CSRF-Token
    response.set_cookie(s.csrf_cookie_name, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="lax", path="/")


def _find_login_user(db: Session, identifier: str) -> User | None:
    ident = identifier.strip().lower()
    col = User.email if "@" in ident else func.lower(User.username)
    return db.scalar(select(User).where(col == ident, User.deleted_at.is_(None)))


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request) or "unknown"
    ident = body.email.strip().lower()
    if not ratelimit.hit(f"login:ip:{ip}", s.login_rate_limit_per_minute) or not ratelimit.hit(
        f"login:email:{ident}", s.login_rate_limit_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again in a minute.")
    user = _find_login_user(db, ident)
    if not verify_password(user.password_hash if user else None, body.password) or not user or not user.is_active:
        audit(db, None, "auth.login_failed", "user", user.id if user else None, request, login=ident)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email, username or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.last_login_at = utcnow()
    token, sess = create_session(db, user, ip, request.headers.get("user-agent"))
    audit(db, user, "auth.login", "user", user.id, request)
    db.commit()
    _set_cookies(response, token, sess.csrf_token)
    return MeOut(user=UserOut.model_validate(user), csrf_token=sess.csrf_token)


@router.post("/logout", response_model=Message)
def logout(response: Response, sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    db_sess = db.get(UserSession, sess.id)
    db_sess.revoked_at = utcnow()
    db.commit()
    s = get_settings()
    response.delete_cookie(s.session_cookie_name, path="/")
    response.delete_cookie(s.csrf_cookie_name, path="/")
    return Message(message="Logged out")


@router.get("/me", response_model=MeOut)
def me(sess: UserSession = Depends(current_session)):
    return MeOut(user=UserOut.model_validate(sess.user), csrf_token=sess.csrf_token)


@router.post("/password", response_model=Message)
def change_password(body: PasswordChange, request: Request, sess: UserSession = Depends(current_session),
                    db: Session = Depends(get_db)):
    user = db.get(User, sess.user_id)
    # a stolen session must not allow guessing the current password at API speed
    if not ratelimit.hit(f"password:user:{user.id}", get_settings().login_rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = utcnow()
    # revoke every other session
    for other in db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.id != sess.id,
                                                      UserSession.revoked_at.is_(None))):
        other.revoked_at = utcnow()
    audit(db, user, "auth.password_changed", "user", user.id, request)
    db.commit()
    return Message(message="Password updated")


@router.post("/sessions/revoke-others", response_model=Message)
def revoke_other_sessions(sess: UserSession = Depends(current_session), db: Session = Depends(get_db)):
    n = 0
    for other in db.scalars(select(UserSession).where(UserSession.user_id == sess.user_id, UserSession.id != sess.id,
                                                      UserSession.revoked_at.is_(None))):
        other.revoked_at = utcnow()
        n += 1
    db.commit()
    return Message(message=f"Revoked {n} other session(s)")


# ---- team ----

@router.get("/team", response_model=list[TeamMemberOut])
def team(_: User = Depends(reader), db: Session = Depends(get_db)):
    """Teammates for owner pickers and filters (active and deactivated; deleted accounts are left out)."""
    name = func.lower(func.coalesce(func.nullif(User.full_name, ""), User.email))
    return list(db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.is_active.desc(), name)))


@router.get("/permissions")
def permission_catalog(_: User = Depends(reader)):
    return permissions.catalog()


# ---- user management (admin) ----

def _revoke_all(db: Session, user_id: uuid.UUID) -> int:
    n = 0
    for s in db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))):
        s.revoked_at = utcnow()
        n += 1
    return n


def _active_admins(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == "admin", User.is_active, User.deleted_at.is_(None))) or 0


def _check_unique(db: Session, user_id: uuid.UUID | None, email: str | None, username: str | None) -> None:
    if email:
        stmt = select(User.id).where(User.email == email)
        if user_id:
            stmt = stmt.where(User.id != user_id)
        if db.scalar(stmt):
            raise HTTPException(409, "A user with this email already exists")
    if username:
        stmt = select(User.id).where(func.lower(User.username) == username.lower())
        if user_id:
            stmt = stmt.where(User.id != user_id)
        if db.scalar(stmt):
            raise HTTPException(409, "This username is taken")


def _user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(404, "User not found")
    return user


def _overrides(role: str, raw: dict | None) -> dict:
    unknown = [k for k in (raw or {}) if k not in permissions.ADJUSTABLE]
    if unknown:
        raise HTTPException(422, f"These permissions can't be adjusted per user: {', '.join(unknown)}")
    return permissions.clean_overrides(role, raw)


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, actor: User = Depends(admin), db: Session = Depends(get_db)):
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    email = body.email.lower()
    _check_unique(db, None, email, body.username)
    user = User(email=email, username=body.username, full_name=body.full_name.strip(), role=body.role,
                password_hash=hash_password(body.password), password_changed_at=utcnow(),
                permission_overrides=_overrides(body.role, body.permission_overrides))
    db.add(user)
    db.flush()
    audit(db, actor, "user.created", "user", user.id, request, role=body.role, username=body.username)
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, actor: User = Depends(admin),
                db: Session = Depends(get_db)):
    user = _user(db, user_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)  # an explicit null never blanks a field
    demoting = changes.get("role", "admin") != "admin" or changes.get("is_active") is False
    if user.id == actor.id and demoting:
        raise HTTPException(400, "You cannot demote or deactivate yourself")
    if user.role == "admin" and user.is_active and demoting and _active_admins(db) <= 1:
        raise HTTPException(400, "At least one active admin is required")
    if "email" in changes:
        changes["email"] = changes["email"].lower()
    _check_unique(db, user.id, changes.get("email"), changes.get("username"))
    if "permission_overrides" in changes or "role" in changes:
        role = changes.get("role", user.role)
        changes["permission_overrides"] = _overrides(role, changes.get("permission_overrides", user.permission_overrides))
    if "full_name" in changes:
        changes["full_name"] = changes["full_name"].strip()
    for k, v in changes.items():
        setattr(user, k, v)
    revoked = _revoke_all(db, user.id) if changes.get("is_active") is False else 0
    audit(db, actor, "user.updated", "user", user.id, request, fields=sorted(changes), role=changes.get("role"),
          is_active=changes.get("is_active"), sessions_revoked=revoked)
    db.commit()
    return user


@router.post("/users/{user_id}/password", response_model=Message)
def reset_password(user_id: uuid.UUID, body: PasswordReset, request: Request, actor: User = Depends(admin),
                   db: Session = Depends(get_db)):
    """Set a new password for a teammate. Every session they have is signed out."""
    user = _user(db, user_id)
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = utcnow()
    revoked = _revoke_all(db, user.id) if user.id != actor.id else 0
    audit(db, actor, "user.password_reset", "user", user.id, request, sessions_revoked=revoked)
    db.commit()
    return Message(message="Password reset. They have been signed out everywhere." if revoked else "Password reset.")


@router.delete("/users/{user_id}", response_model=Message)
def delete_user(user_id: uuid.UUID, request: Request, reassign_to: uuid.UUID | None = None, actor: User = Depends(admin),
                db: Session = Depends(get_db)):
    """Delete an account. They are signed out and can never sign in again; their name stays on history. Their
    opportunities and open tasks go to ``reassign_to``, or become unassigned."""
    from app.services import opportunities as opp_service

    user = _user(db, user_id)
    if user.id == actor.id:
        raise HTTPException(400, "You cannot delete your own account")
    if user.role == "admin" and user.is_active and _active_admins(db) <= 1:
        raise HTTPException(400, "At least one active admin is required")
    target = None
    if reassign_to:
        target = _user(db, reassign_to)
        if target.id == user.id or not target.is_active:
            raise HTTPException(422, "Choose an active teammate to take over their opportunities")
    moved = opp_service.reassign_all(db, user, target, actor)
    tasks_moved = 0
    for t in db.scalars(select(CrmTask).where(CrmTask.assignee_id == user.id, CrmTask.completed_at.is_(None))):
        t.assignee_id = target.id if target else None
        tasks_moved += 1
    revoked = _revoke_all(db, user.id)
    old_email = user.email
    user.deleted_at = utcnow()
    user.is_active = False
    user.email = f"deleted-{user.id.hex}@deleted.invalid"  # frees the address for a new account
    user.username = None
    user.password_hash = hash_password(secrets.token_urlsafe(32))
    user.permission_overrides = {}
    audit(db, actor, "user.deleted", "user", user.id, request, email=old_email, opportunities_moved=moved,
          tasks_moved=tasks_moved, reassigned_to=str(target.id) if target else None, sessions_revoked=revoked)
    db.commit()
    where = f"to {target.full_name or target.email}" if target else "to Unassigned"
    noun = "opportunity" if moved == 1 else "opportunities"
    return Message(message=f"Deleted. {moved} {noun} moved {where}.")
