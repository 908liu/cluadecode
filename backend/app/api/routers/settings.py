"""Per-user model service configuration endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import ApiError, not_found, validation_error
from ...core.net import OutboundUrlError, check_outbound_url
from ...core.security import encrypt_key
from ...core.utils import new_id, utcnow_iso
from ...db.models import ApiService, ChangeLog, Document, IndexVersion, User
from ...services import upstream
from ..deps import get_current_user, get_db, get_settings
from ..responses import ok
from ..schemas import EnabledPayload, ServiceCreate, ServiceUpdate
from ..serializers import service_public, system_status

router = APIRouter(prefix="/settings", tags=["settings"])


def _log(db: Session, user: User, action: str, target_id: str, target_name: str, summary: str) -> None:
    db.add(ChangeLog(
        id=new_id("chg"), user_id=user.id, actor=user.display_name, action=action,
        target_type="api_service", target_id=target_id, target_name=target_name,
        summary=summary, created_at=utcnow_iso(),
    ))


def _name_conflict(db: Session, user_id: str, service_type: str, name: str, exclude: str | None = None) -> bool:
    query = select(func.count(ApiService.id)).where(
        ApiService.user_id == user_id, ApiService.type == service_type,
        func.lower(ApiService.name) == name.casefold(),
    )
    if exclude:
        query = query.where(ApiService.id != exclude)
    return bool(db.scalar(query))


def _mark_reindex(db: Session, user_id: str) -> None:
    db.execute(
        update(Document).where(Document.user_id == user_id, Document.status == "available",
                               Document.deleted_at.is_(None))
        .values(reindex_required=True, updated_at=utcnow_iso())
    )


def _ensure_allowed_url(settings: Settings, base_url: str) -> None:
    try:
        check_outbound_url(
            base_url,
            allow_private=not settings.block_private_upstreams,
            allowed_hosts=settings.model_host_allowlist,
        )
    except OutboundUrlError as exc:
        raise validation_error([{"field": "base_url", "message": str(exc)}]) from exc


def _active_index(db: Session, user_id: str) -> IndexVersion | None:
    return db.scalar(select(IndexVersion).where(IndexVersion.user_id == user_id, IndexVersion.is_active.is_(True)))


@router.get("/services")
def list_services(user: User = Depends(get_current_user), settings: Settings = Depends(get_settings),
                  db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ApiService).where(ApiService.user_id == user.id).order_by(ApiService.type, ApiService.created_at)
    ).all()
    return ok({"items": [service_public(settings, row) for row in rows]})


@router.post("/services")
def create_service(payload: ServiceCreate, user: User = Depends(get_current_user),
                   settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    if _name_conflict(db, user.id, payload.type, payload.name):
        raise validation_error([{"field": "name", "message": "同类型内服务名称已存在"}])
    _ensure_allowed_url(settings, payload.base_url)
    now = utcnow_iso()
    row = ApiService(
        id=new_id("s"), user_id=user.id, name=payload.name, type=payload.type,
        base_url=payload.base_url, model=payload.model,
        api_key_cipher=encrypt_key(settings, payload.api_key), timeout_seconds=payload.timeout_seconds,
        dimensions=payload.dimensions, protocol=payload.protocol or "cohere",
        enabled=True, is_default=False, created_at=now, updated_at=now,
    )
    db.add(row)
    _log(db, user, "create", row.id, row.name, "新增服务")
    db.commit()
    return ok(service_public(settings, row))


@router.put("/services/{service_id}")
def update_service(service_id: str, payload: ServiceUpdate, user: User = Depends(get_current_user),
                   settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user.id:
        raise not_found("api_service", service_id)
    if payload.type and payload.type != row.type:
        raise validation_error([{"field": "type", "message": "服务类型不可修改"}])
    if _name_conflict(db, user.id, row.type, payload.name, exclude=row.id):
        raise validation_error([{"field": "name", "message": "同类型内服务名称已存在"}])
    _ensure_allowed_url(settings, payload.base_url)
    if row.type == "embedding" and payload.dimensions is None:
        raise validation_error([{"field": "dimensions", "message": "Embedding 服务必须填写 dimensions"}])
    if row.type != "embedding" and payload.dimensions is not None:
        raise validation_error([{"field": "dimensions", "message": "只有 Embedding 服务可以填写 dimensions"}])
    if payload.protocol is not None and row.type != "rerank":
        raise validation_error([{"field": "protocol", "message": "只有 Rerank 服务可以填写 protocol"}])
    embedding_changed = row.type == "embedding" and (row.model != payload.model or row.dimensions != payload.dimensions)
    row.name, row.base_url, row.model = payload.name, payload.base_url, payload.model
    row.timeout_seconds, row.dimensions = payload.timeout_seconds, payload.dimensions
    if payload.protocol is not None:
        row.protocol = payload.protocol
    if payload.api_key:
        row.api_key_cipher = encrypt_key(settings, payload.api_key)
    row.updated_at = utcnow_iso()
    if embedding_changed and row.is_default:
        _mark_reindex(db, user.id)
    _log(db, user, "update", row.id, row.name, "更新服务配置")
    db.commit()
    return ok(service_public(settings, row))


@router.delete("/services/{service_id}")
def delete_service(service_id: str, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user.id:
        raise not_found("api_service", service_id)
    if row.is_default:
        raise ApiError(409, 40902, "默认服务不能直接删除，请先设置同类型新的默认服务",
                       {"type": row.type, "action": "delete"})
    _log(db, user, "delete", row.id, row.name, "删除服务")
    db.delete(row)
    db.commit()
    return ok({"id": service_id, "deleted": True})


@router.post("/services/{service_id}/test")
def test_service(service_id: str, user: User = Depends(get_current_user),
                 settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user.id:
        raise not_found("api_service", service_id)
    service = upstream.service_by_id(db, settings, user.id, service_id)
    if service is None:
        raise not_found("api_service", service_id)
    return ok(upstream.test_service(service))


@router.post("/services/{service_id}/default")
def set_default(service_id: str, user: User = Depends(get_current_user),
                settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user.id:
        raise not_found("api_service", service_id)
    if not row.enabled:
        raise ApiError(409, 40903, "服务已停用，不能设为默认", {"type": row.type})
    embedding_changed = row.type == "embedding"
    active = _active_index(db, user.id)
    if embedding_changed and active is not None:
        embedding_changed = active.model != row.model or active.dimensions != row.dimensions
    for other in db.scalars(select(ApiService).where(ApiService.user_id == user.id, ApiService.type == row.type)).all():
        other.is_default = other.id == row.id
        other.updated_at = utcnow_iso()
    if embedding_changed:
        _mark_reindex(db, user.id)
    _log(db, user, "set_default", row.id, row.name, "设为默认服务")
    db.commit()
    return ok(system_status(db, settings, user.id))


@router.patch("/services/{service_id}/enabled")
def set_enabled(service_id: str, payload: EnabledPayload, user: User = Depends(get_current_user),
                settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    row = db.get(ApiService, service_id)
    if row is None or row.user_id != user.id:
        raise not_found("api_service", service_id)
    if not payload.enabled and row.is_default:
        raise ApiError(409, 40902, "默认服务不能直接停用，请先设置同类型新的默认服务",
                       {"type": row.type, "action": "disable"})
    row.enabled = payload.enabled
    row.updated_at = utcnow_iso()
    _log(db, user, "enable" if payload.enabled else "disable", row.id, row.name,
         "启用服务" if payload.enabled else "停用服务")
    db.commit()
    return ok(service_public(settings, row))


@router.get("/changes")
def list_changes(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(
        select(ChangeLog).where(ChangeLog.user_id == user.id).order_by(ChangeLog.created_at.desc()).limit(100)
    ).all()
    return ok({"items": [
        {"id": row.id, "actor": row.actor, "action": row.action, "target_type": row.target_type,
         "target_name": row.target_name, "created_at": row.created_at, "summary": row.summary}
        for row in rows
    ]})