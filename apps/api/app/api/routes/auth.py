import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, client_ip, current_session
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (
    create_session,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models import User, UserSession
from app.models.base import utcnow
from app.schemas.auth import LoginIn, MeOut, PasswordChange, UserCreate, UserOut, UserUpdate
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


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request) or "unknown"
    email = body.email.lower()
    if not ratelimit.hit(f"login:ip:{ip}", s.login_rate_limit_per_minute) or not ratelimit.hit(
        f"login:email:{email}", s.login_rate_limit_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again in a minute.")
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, body.password) or not user or not user.is_active:
        audit(db, None, "auth.login_failed", "user", user.id if user else None, request, email=email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
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


# ---- user management (admin) ----

@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(admin), db: Session = Depends(get_db)):
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, actor: User = Depends(admin), db: Session = Depends(get_db)):
    try:
        validate_password_strength(body.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(email=email, full_name=body.full_name, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    audit(db, actor, "user.created", "user", user.id, request, role=body.role)
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, actor: User = Depends(admin),
                db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    changes = body.model_dump(exclude_unset=True, exclude_none=True)  # an explicit null never blanks a field
    if user.id == actor.id and (changes.get("role", "admin") != "admin" or changes.get("is_active") is False):
        raise HTTPException(400, "You cannot demote or deactivate yourself")
    if user.role == "admin" and (changes.get("role", "admin") != "admin" or changes.get("is_active") is False):
        admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active))
        if admins <= 1:
            raise HTTPException(400, "At least one active admin is required")
    for k, v in changes.items():
        setattr(user, k, v)
    if changes.get("is_active") is False:
        for s in db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))):
            s.revoked_at = utcnow()
    audit(db, actor, "user.updated", "user", user.id, request, **changes)
    db.commit()
    return user
