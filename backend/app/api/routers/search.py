"""Search debug and per-user search settings (P1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import service_missing
from ...db.models import AppSetting, UserSetting, User
from ..deps import get_current_user, get_db, get_registry, get_settings
from ..responses import ok
from ..schemas import SearchDebugRequest, SearchSettingsPayload
from ...services.index_cache import IndexCacheRegistry

router = APIRouter(prefix="/search", tags=["search"])


def _setting(db: Session, user_id: str, key: str, default: str) -> str:
    row = db.get(UserSetting, (user_id, key))
    if row and row.value != "":
        return row.value
    global_row = db.get(AppSetting, key)
    return global_row.value if global_row and global_row.value != "" else default


def _set(db: Session, user_id: str, key: str, value: str) -> None:
    row = db.get(UserSetting, (user_id, key))
    if row:
        row.value = value
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value))


def _payload(db: Session, user_id: str, settings: Settings) -> dict:
    return {
        "top_k": int(_setting(db, user_id, "search.top_k", str(settings.default_top_k))),
        "rrf_k": int(_setting(db, user_id, "search.rrf_k", str(settings.rrf_k))),
        "use_rerank": _setting(db, user_id, "search.use_rerank", "false").strip().lower()
        in ("1", "true", "yes", "on"),
        "rerank_top_n": int(_setting(db, user_id, "search.rerank_top_n", str(settings.rerank_top_n))),
    }


@router.post("/debug")
def debug(
    payload: SearchDebugRequest,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    registry: IndexCacheRegistry = Depends(get_registry),
) -> dict:
    snapshot = registry.get(db, user.id)
    if not snapshot.has_index:
        raise service_missing("embedding")
    threshold = float(_setting(db, user.id, "search.threshold", str(settings.score_threshold)))
    rrf = int(_setting(db, user.id, "search.rrf_k", str(settings.rrf_k)))
    result = registry.retrieve(
        db, settings, user.id, payload.query, payload.top_k, threshold, rrf_k=rrf,
        snapshot=snapshot, use_rerank=payload.use_rerank,
        rerank_top_n=payload.rerank_top_n,
    )
    return ok({"keyword": result["keyword"], "vector": result["vector"],
               "fused": result["fused"], "reranked": result["reranked"],
               "final": result["final"], "rerank": result["rerank"]})


@router.get("/settings")
def get_settings_endpoint(user: User = Depends(get_current_user),
                          settings: Settings = Depends(get_settings),
                          db: Session = Depends(get_db)) -> dict:
    return ok(_payload(db, user.id, settings))


@router.put("/settings")
def update_settings(payload: SearchSettingsPayload, user: User = Depends(get_current_user),
                    settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    _set(db, user.id, "search.top_k", str(payload.top_k))
    _set(db, user.id, "search.rrf_k", str(payload.rrf_k))
    _set(db, user.id, "search.use_rerank", "true" if payload.use_rerank else "false")
    _set(db, user.id, "search.rerank_top_n", str(payload.rerank_top_n))
    db.commit()
    return ok(_payload(db, user.id, settings))