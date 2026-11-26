"""System status and health endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.utils import utcnow_iso
from ...db.models import IndexVersion, User
from ...db.session import Database
from ..deps import get_current_user, get_db, get_settings
from ..responses import ok
from ..serializers import system_status

router = APIRouter(tags=["system"])


@router.get("/settings/status")
def status(
    _user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
) -> dict:
    return ok(system_status(db, settings, _user.id))


def health_payload(db: Session) -> dict:
    database = "ok"
    index = "empty"
    try:
        db.execute(text("SELECT 1"))
        active = int(db.scalar(select(func.count(IndexVersion.id)).where(IndexVersion.is_active.is_(True))) or 0)
        index = "ok" if active else "empty"
    except Exception:
        database = "error"
        index = "degraded"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "version": "1.0.0",
        "database": database,
        "index": index,
        "time": utcnow_iso(),
    }


@router.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    return health_payload(db)