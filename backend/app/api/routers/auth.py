"""Authentication endpoints. Registration is controlled by REGISTRATION_MODE."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import ApiError, unauthorized
from ...core.security import create_token, hash_password, verify_password
from ...core.utils import new_id, utcnow_iso
from ...db.models import User
from ..deps import RateLimiter, client_ip, get_db, get_current_user, get_settings, rate_limit_error
from ..responses import ok
from ..schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.5c1Vn3S3x4xF5g7n5n5n5n5n5n5n5n5n"


def _auth_payload(settings: Settings, user: User) -> dict:
    token, expires_in = create_token(settings, user.id)
    return {
        "access_token": token, "token_type": "bearer", "expires_in": expires_in,
        "user": {
            "id": user.id, "username": user.username,
            "display_name": user.display_name, "is_admin": user.is_admin,
        },
    }


def _registration_allowed(settings: Settings, user_count: int, normal_user_count: int) -> bool:
    if user_count == 0:
        if settings.registration_mode == "invite":
            return bool(settings.registration_invite_code)
        return settings.registration_mode in ("open", "first-user")
    if normal_user_count >= settings.max_normal_users:
        return False
    if settings.registration_mode == "open":
        return True
    if settings.registration_mode == "invite":
        return bool(settings.registration_invite_code)
    return False


def _user_counts(db: Session) -> tuple[int, int]:
    user_count = int(db.scalar(select(func.count(User.id))) or 0)
    normal_user_count = int(
        db.scalar(select(func.count(User.id)).where(User.is_admin.is_(False))) or 0
    )
    return user_count, normal_user_count


@router.get("/registration-status")
def registration_status(
    settings: Settings = Depends(get_settings), db: Session = Depends(get_db)
) -> dict:
    count, normal_count = _user_counts(db)
    return ok({
        "registered": count > 0,
        "mode": settings.registration_mode,
        "registration_allowed": _registration_allowed(settings, count, normal_count),
        "invite_required": settings.registration_mode == "invite",
        "normal_users": normal_count,
        "max_normal_users": settings.max_normal_users,
        "normal_user_slots_remaining": max(0, settings.max_normal_users - normal_count),
    })


@router.post("/register")
def register(
    payload: RegisterRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    limiter: RateLimiter = request.app.state.register_limiter
    key = client_ip(request, settings)
    retry = limiter.retry_after(key, settings.register_rate_limit, settings.register_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    limiter.hit(key)

    # Serialize registration checks across concurrent requests so the configured cap is hard.
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    count, normal_count = _user_counts(db)
    if not _registration_allowed(settings, count, normal_count):
        raise ApiError(403, 40301, "注册已关闭", None)
    if settings.registration_mode == "invite":
        code = (payload.invite_code or "").strip()
        if not settings.registration_invite_code or not secrets.compare_digest(
            code, settings.registration_invite_code
        ):
            raise ApiError(403, 40302, "邀请码不正确", None)
    existing = db.scalar(select(User).where(func.lower(User.username) == payload.username.casefold()))
    if existing is not None:
        raise ApiError(409, 40908, "用户名已存在，请换一个用户名", None)
    display_name = payload.display_name or payload.username[:32]
    user = User(
        id=new_id("u"), username=payload.username,
        is_admin=count == 0,
        password_hash=hash_password(payload.password, settings.bcrypt_rounds),
        display_name=display_name[:32], created_at=utcnow_iso(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(409, 40908, "用户名已存在，请换一个用户名", None) from exc
    return ok(_auth_payload(settings, user))


@router.post("/login")
def login(
    payload: LoginRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    limiter: RateLimiter = request.app.state.login_limiter
    key = f"{client_ip(request, settings)}:{payload.username.casefold()}"
    retry = limiter.retry_after(key, settings.login_rate_limit, settings.login_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    user = db.scalar(select(User).where(func.lower(User.username) == payload.username.casefold()))
    ok_password = verify_password(payload.password, user.password_hash) if user else False
    if user is None or not ok_password:
        limiter.hit(key)
        raise unauthorized("用户名或密码错误")
    limiter.reset(key)
    return ok(_auth_payload(settings, user))


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return ok({
        "id": user.id, "username": user.username,
        "display_name": user.display_name, "is_admin": user.is_admin,
    })


@router.post("/logout")
def logout(_user: User = Depends(get_current_user)) -> dict:
    return ok(None)