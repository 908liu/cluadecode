"""QA ask and per-user QA records."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import not_found
from ...core.utils import load_json
from ...db.models import QaRecord, User
from ..deps import (
    RateLimiter, get_current_user, get_db, get_registry, get_settings, rate_limit_error,
)
from ..responses import ok
from ..schemas import AskRequest
from ...services import qa_service
from ...services.index_cache import IndexCacheRegistry

router = APIRouter(prefix="/qa", tags=["qa"])


def _record_public(row: QaRecord) -> dict:
    return {
        "id": row.id, "question": row.question, "answer": row.answer,
        "refused": bool(row.refused), "refusal_reason": row.refusal_reason,
        "model": row.model, "created_at": row.created_at,
    }


@router.post("/ask")
def ask(
    payload: AskRequest,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    registry: IndexCacheRegistry = Depends(get_registry),
) -> dict:
    limiter: RateLimiter = request.app.state.ask_limiter
    retry = limiter.retry_after(user.id, settings.ask_rate_limit, settings.ask_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    limiter.hit(user.id)
    outcome = qa_service.ask(
        db, settings, registry, user.id, payload.question,
        getattr(request.state, "request_id", ""), persist=True,
    )
    return ok(outcome.response)


@router.post("/ask/stream")
def ask_stream(
    payload: AskRequest,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    registry: IndexCacheRegistry = Depends(get_registry),
) -> StreamingResponse:
    limiter: RateLimiter = request.app.state.ask_limiter
    retry = limiter.retry_after(user.id, settings.ask_rate_limit, settings.ask_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    limiter.hit(user.id)
    request_id = getattr(request.state, "request_id", "")

    def event_stream():
        try:
            for event, data in qa_service.ask_stream(
                db, settings, registry, user.id, payload.question, request_id, persist=True,
            ):
                yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception:
            error_payload = {
                "code": 50001,
                "status": 500,
                "message": "流式回答失败",
                "detail": None,
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/records")
def list_records(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    total = int(db.scalar(select(func.count(QaRecord.id)).where(QaRecord.user_id == user.id)) or 0)
    rows = db.scalars(
        select(QaRecord).where(QaRecord.user_id == user.id)
        .order_by(QaRecord.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return ok({"items": [_record_public(row) for row in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/records/{record_id}")
def get_record(record_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(QaRecord, record_id)
    if row is None or row.user_id != user.id:
        raise not_found("qa_record", record_id)
    data = _record_public(row)
    data["citations"] = load_json(row.citations_json, [])
    return ok(data)


@router.delete("/records/{record_id}")
def delete_record(record_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    row = db.get(QaRecord, record_id)
    if row is None or row.user_id != user.id:
        raise not_found("qa_record", record_id)
    db.delete(row)
    db.commit()
    return ok(None)