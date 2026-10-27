import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import admin, audit, client_ip, current_session
from app.core import mfa, ratelimit
from app.core.config import get_settings
from app.core.crypto import decrypt, encrypt
from app.core.db import get_db
from app.core.security import (
    create_session,
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.models import AuditLog, User, UserSession
from app.models.base import utcnow
from app.schemas.auth import (
    AuditEntryOut,
    LoginIn,
    MeOut,
    MfaChallengeOut,
    MfaDisableIn,
    MfaEnableIn,
    MfaLoginIn,
    MfaSetupIn,
    PasswordChange,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.schemas.common import Message
from app.services import security_alerts

router = APIRouter(prefix="/api/auth", tags=["auth"])

FAILED_SIGN_INS = ("auth.login_failed", "auth.mfa_failed")
# What the Security log in Settings shows (everything else stays in the full audit log)
SECURITY_ACTIONS = ("auth.", "user.", "authz.", "mailbox.", "settings.", "contacts.exported")


def _set_cookies(response: Response, token: str, csrf: str) -> None:
    s = get_settings()
    max_age = s.session_ttl_hours * 3600
    response.set_cookie(s.session_cookie_name, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", path="/")
    # Readable by JS so the SPA can echo it in X-CSRF-Token
    response.set_cookie(s.csrf_cookie_name, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="lax", path="/")


def _me(user: User, csrf: str) -> MeOut:
    return MeOut(user=UserOut.model_validate(user), csrf_token=csrf,
                 mfa_setup_required=mfa.required_for(user) and not user.mfa_enabled)


def _recent_failures(db: Session, email: str, ip: str, user: User | None) -> tuple[int, int]:
    """Failed sign-ins (password or code) within the lockout window: (for this email, from this IP)."""
    since = utcnow() - timedelta(minutes=get_settings().login_lockout_minutes)
    base = select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(FAILED_SIGN_INS),
                                                            AuditLog.created_at > since)
    by_email = base.where(AuditLog.data["email"].astext == email)
    if user and user.last_login_at:  # a successful sign-in clears the count
        by_email = by_email.where(AuditLog.created_at > user.last_login_at)
    return db.scalar(by_email) or 0, db.scalar(base.where(AuditLog.ip_address == ip)) or 0


def _check_lockout(db: Session, request: Request, email: str, ip: str, user: User | None) -> None:
    s = get_settings()
    per_email, per_ip = _recent_failures(db, email, ip, user)
    if per_email < s.login_lockout_failures and per_ip < s.login_lockout_failures * 3:
        return
    audit(db, None, "auth.locked_out", "user", user.id if user else None, request, email=email,
          failures_email=per_email, failures_ip=per_ip)
    security_alerts.notify_admins(
        db, f"Sign-in locked: {email}",
        f"{per_email} failed sign-ins for {email} and {per_ip} from {ip} in the last {s.login_lockout_minutes} "
        "minutes. Sign-in is paused for this account. If this wasn't you or your team, someone may be guessing "
        "passwords.", throttle_key=f"lockout:{email}")
    db.commit()
    raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                        f"Too many failed sign-ins. Try again in {s.login_lockout_minutes} minutes.")


def _start_session(db: Session, request: Request, response: Response, user: User, method: str) -> MeOut:
    user.last_login_at = utcnow()
    token, sess = create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    audit(db, user, "auth.login", "user", user.id, request, method=method)
    db.commit()
    _set_cookies(response, token, sess.csrf_token)
    return _me(user, sess.csrf_token)


@router.post("/login", response_model=MeOut | MfaChallengeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    ip = client_ip(request) or "unknown"
    email = body.email.lower()
    if not ratelimit.hit(f"login:ip:{ip}", s.login_rate_limit_per_minute) or not ratelimit.hit(
        f"login:email:{email}", s.login_rate_limit_per_minute
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again in a minute.")
    user = db.scalar(select(User).where(User.email == email))
    _check_lockout(db, request, email, ip, user)
    if not verify_password(user.password_hash if user else None, body.password) or not user or not user.is_active:
        audit(db, None, "auth.login_failed", "user", user.id if user else None, request, email=email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    if user.mfa_enabled:
        db.commit()  # the challenge is bound to the (possibly rehashed) password
        return MfaChallengeOut(mfa_token=mfa.issue_challenge(user))
    return _start_session(db, request, response, user, "password")


@router.post("/login/mfa", response_model=MeOut)
def login_mfa(body: MfaLoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Second step of sign-in: the code from the authenticator app, or a recovery code."""
    expired = HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign-in expired. Enter your password again.")
    uid = mfa.challenge_user_id(body.mfa_token)
    if uid is None:
        raise expired
    try:
        user_id = uuid.UUID(uid)
    except ValueError:
        raise expired from None
    # row lock: two requests with the same code can't both burn the same time step
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None or not user.is_active or not user.mfa_enabled or not mfa.challenge_matches(body.mfa_token, user):
        raise expired
    ip = client_ip(request) or "unknown"
    if not ratelimit.hit(f"mfa:user:{user.id}", 5):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
    _check_lockout(db, request, user.email, ip, user)
    method = mfa.verify_second_factor(user, body.code)
    if method is None:
        audit(db, None, "auth.mfa_failed", "user", user.id, request, email=user.email)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That code didn't work. Check your authenticator app.")
    if method == "recovery":
        left = len(user.recovery_code_hashes)
        audit(db, user, "auth.recovery_code_used", "user", user.id, request, remaining=left)
        security_alerts.notify_admins(db, f"Recovery code used: {user.email}",
                                      f"{user.email} signed in with a recovery code ({left} left).")
    return _start_session(db, request, response, user, method)


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, sess: UserSession = Depends(current_session),
           db: Session = Depends(get_db)):
    db_sess = db.get(UserSession, sess.id)
    db_sess.revoked_at = utcnow()
    audit(db, sess.user, "auth.logout", "user", sess.user_id, request)
    db.commit()
    s = get_settings()
    response.delete_cookie(s.session_cookie_name, path="/")
    response.delete_cookie(s.csrf_cookie_name, path="/")
    return Message(message="Logged out")


@router.get("/me", response_model=MeOut)
def me(sess: UserSession = Depends(current_session)):
    return _me(sess.user, sess.csrf_token)


def _revoke_others(db: Session, user_id: uuid.UUID, keep: uuid.UUID | None) -> int:
    n = 0
    for other in db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.id != keep,
                                                      UserSession.revoked_at.is_(None))):
        other.revoked_at = utcnow()
        n += 1
    return n


def _confirm_password(user: User, password: str) -> None:
    # a stolen session must not allow guessing the current password at API speed
    if not ratelimit.hit(f"password:user:{user.id}", get_settings().login_rate_limit_per_minute):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
    if not verify_password(user.password_hash, password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")


@router.post("/password", response_model=Message)
def change_password(body: PasswordChange, request: Request, sess: UserSession = Depends(current_session),
                    db: Session = Depends(get_db)):
    user = db.get(User, sess.user_id)
    _confirm_password(user, body.current_password)
    try:
        validate_password_strength(body.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    user.password_hash = hash_password(body.new_password)
    _revoke_others(db, user.id, sess.id)
    audit(db, user, "auth.password_changed", "user", user.id, request)
    db.commit()
    return Message(message="Password updated")


@router.post("/sessions/revoke-others", response_model=Message)
def revoke_other_sessions(request: Request, sess: UserSession = Depends(current_session),
                          db: Session = Depends(get_db)):
    n = _revoke_others(db, sess.user_id, sess.id)
    audit(db, sess.user, "auth.sessions_revoked", "user", sess.user_id, request, count=n)
    db.commit()
    return Message(message=f"Revoked {n} other session(s)")


# ---- two-step verification ----

@router.post("/mfa/setup")
def mfa_setup(body: MfaSetupIn, request: Request, sess: UserSession = Depends(current_session),
              db: Session = Depends(get_db)):
    """Starts enrolment: a new secret (stored encrypted, inactive until /mfa/enable confirms a code)."""
    user = db.get(User, sess.user_id)
    if user.mfa_enabled:
        raise HTTPException(409, "Two-step verification is already on")
    _confirm_password(user, body.password)
    secret = mfa.new_secret()
    user.totp_secret_enc = encrypt(secret)
    user.totp_last_step = None
    audit(db, user, "auth.mfa_setup_started", "user", user.id, request)
    db.commit()
    return mfa.provisioning(user, secret)


@router.post("/mfa/enable")
def mfa_enable(body: MfaEnableIn, request: Request, sess: UserSession = Depends(current_session),
               db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.id == sess.user_id).with_for_update())
    if user.mfa_enabled:
        raise HTTPException(409, "Two-step verification is already on")
    pending = decrypt(user.totp_secret_enc) if user.totp_secret_enc else None
    if not pending:
        raise HTTPException(409, "Start setup first")
    if not ratelimit.hit(f"mfa:user:{user.id}", 5):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again in a minute.")
    if not mfa.verify_totp(user, body.code, secret=pending):
        db.commit()
        raise HTTPException(400, "That code didn't work. Check the time on your phone and try the newest code.")
    codes = mfa.enable(user, pending)
    revoked = _revoke_others(db, user.id, sess.id)
    audit(db, user, "auth.mfa_enabled", "user", user.id, request, other_sessions_revoked=revoked)
    db.commit()
    return {"recovery_codes": codes, "user": UserOut.model_validate(user)}


@router.post("/mfa/disable", response_model=Message)
def mfa_disable(body: MfaDisableIn, request: Request, sess: UserSession = Depends(current_session),
                db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.id == sess.user_id).with_for_update())
    if not user.mfa_enabled:
        raise HTTPException(409, "Two-step verification is not on")
    _confirm_password(user, body.password)
    if mfa.verify_second_factor(user, body.code) is None:
        audit(db, user, "auth.mfa_failed", "user", user.id, request, email=user.email, during="disable")
        db.commit()
        raise HTTPException(400, "That code didn't work")
    mfa.disable(user)
    audit(db, user, "auth.mfa_disabled", "user", user.id, request)
    security_alerts.notify_admins(db, f"Two-step verification turned off: {user.email}",
                                  f"{user.email} ({user.role}) turned off two-step verification.")
    db.commit()
    return Message(message="Two-step verification is off")


@router.post("/mfa/recovery-codes")
def mfa_new_recovery_codes(body: MfaDisableIn, request: Request, sess: UserSession = Depends(current_session),
                           db: Session = Depends(get_db)):
    """Replaces all recovery codes (the old ones stop working)."""
    user = db.scalar(select(User).where(User.id == sess.user_id).with_for_update())
    if not user.mfa_enabled:
        raise HTTPException(409, "Two-step verification is not on")
    _confirm_password(user, body.password)
    if not mfa.verify_totp(user, body.code):
        db.commit()
        raise HTTPException(400, "That code didn't work")
    codes = mfa.new_recovery_codes(user)
    audit(db, user, "auth.recovery_codes_replaced", "user", user.id, request)
    db.commit()
    return {"recovery_codes": codes}


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
    if body.role == "admin":
        security_alerts.notify_admins(db, f"New admin: {email}", f"{actor.email} created an admin account for {email}.")
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
    promoted = changes.get("role") == "admin" and user.role != "admin"
    for k, v in changes.items():
        setattr(user, k, v)
    if changes.get("is_active") is False:
        _revoke_others(db, user.id, None)
    audit(db, actor, "user.updated", "user", user.id, request, **changes)
    if promoted:
        security_alerts.notify_admins(db, f"New admin: {user.email}", f"{actor.email} made {user.email} an admin.")
    elif changes.get("is_active") is False:
        security_alerts.notify_admins(db, f"User deactivated: {user.email}",
                                      f"{actor.email} deactivated {user.email}; their sessions were ended.")
    db.commit()
    return user


@router.post("/users/{user_id}/mfa/reset", response_model=UserOut)
def reset_user_mfa(user_id: uuid.UUID, request: Request, actor: User = Depends(admin), db: Session = Depends(get_db)):
    """For a teammate who lost their phone and recovery codes. Ends their sessions; they set it up again."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == actor.id:
        raise HTTPException(400, "Use your recovery codes, or turn it off yourself in Settings → Security")
    mfa.disable(user)
    _revoke_others(db, user.id, None)
    audit(db, actor, "user.mfa_reset", "user", user.id, request)
    security_alerts.notify_admins(db, f"Two-step verification reset: {user.email}",
                                  f"{actor.email} reset two-step verification for {user.email}.")
    db.commit()
    return user


@router.get("/audit-log", response_model=list[AuditEntryOut])
def audit_log(security_only: bool = True, limit: int = Query(100, ge=1, le=500), _: User = Depends(admin),
              db: Session = Depends(get_db)):
    stmt = select(AuditLog, User.email).outerjoin(User, User.id == AuditLog.user_id)
    if security_only:
        stmt = stmt.where(or_(*[AuditLog.action.startswith(p) for p in SECURITY_ACTIONS]))
    rows = db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()
    return [AuditEntryOut(id=a.id, created_at=a.created_at, action=a.action, user_email=email,
                          entity_type=a.entity_type, entity_id=a.entity_id, ip_address=a.ip_address,
                          data=a.data or {}) for a, email in rows]
