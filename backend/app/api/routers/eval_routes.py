"""Per-user evaluation endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import ApiError, not_found, validation_error
from ...core.utils import dump_json, load_json, new_id, utcnow_iso
from ...db.models import ApiService, EvalCase, EvalDataset, EvalRun, EvalRunCase, User
from ..deps import (
    get_current_user,
    get_db,
    get_registry,
    get_settings,
    get_task_queue,
    rate_limit_error,
)
from ..responses import ok
from ..schemas import EvalRunCreate, HumanScoreRequest
from ..serializers import dataset_public, eval_case_human, eval_case_model, eval_run_public
from ...services import eval_service, upstream
from ...services.index_cache import IndexCacheRegistry
from ...services.task_queue import TaskQueue

router = APIRouter(prefix="/eval", tags=["evaluation"])


def _enforce_eval_limit(request: Request, settings: Settings, user: User) -> None:
    limiter = request.app.state.eval_limiter
    retry = limiter.retry_after(user.id, settings.eval_rate_limit, settings.eval_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    limiter.hit(user.id)


@router.get("/datasets")
def datasets(_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(EvalDataset).order_by(EvalDataset.id)).all()
    return ok({"items": [dataset_public(row) for row in rows]})


@router.get("/runs")
def list_runs(dataset_id: str | None = Query(default=None), user: User = Depends(get_current_user),
              db: Session = Depends(get_db)) -> dict:
    query = select(EvalRun).where(EvalRun.user_id == user.id).order_by(EvalRun.created_at.desc())
    if dataset_id:
        query = query.where(EvalRun.dataset_id == dataset_id)
    return ok({"items": [eval_run_public(row) for row in db.scalars(query.limit(50)).all()]})


@router.post("/runs")
def create_run(request: Request, payload: EvalRunCreate, user: User = Depends(get_current_user),
               settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
               registry: IndexCacheRegistry = Depends(get_registry), tasks: TaskQueue = Depends(get_task_queue)) -> dict:
    _enforce_eval_limit(request, settings, user)
    dataset = db.get(EvalDataset, payload.dataset_id)
    if dataset is None:
        raise not_found("eval_dataset", payload.dataset_id)
    judge_row = db.get(ApiService, payload.judge_service_id)
    if judge_row is None or judge_row.user_id != user.id or judge_row.type != "chat" or not judge_row.enabled:
        raise ApiError(409, 40903, "评分服务不可用", {"type": "judge"})
    if upstream.effective_service(db, settings, user.id, "chat") is None:
        raise ApiError(409, 40903, "默认对话服务未配置", {"type": "chat"})
    if upstream.effective_service(db, settings, user.id, "embedding") is None:
        raise ApiError(503, 50302, "Embedding 服务未配置", {"type": "embedding"})
    snapshot = registry.get(db, user.id)
    if not snapshot.has_index:
        raise ApiError(503, 50302, "索引尚未就绪，请先上传并处理文档", {"type": "embedding"})
    running = db.scalar(select(EvalRun).where(
        EvalRun.user_id == user.id, EvalRun.dataset_id == dataset.id,
        EvalRun.status.in_(["queued", "running"]),
    ))
    if running is not None:
        raise ApiError(409, 40904, "该数据集已有评测任务在运行",
                       {"dataset_id": dataset.id, "running_run_id": running.id})
    snapshot_config = eval_service.build_snapshot(
        db, settings, registry, user.id, dataset, payload.top_k, payload.threshold,
        payload.judge_service_id, use_rerank=payload.use_rerank,
        rerank_top_n=payload.rerank_top_n,
        retrieval_strategy=payload.retrieval_strategy,
    )
    now = utcnow_iso()
    run = EvalRun(
        id=new_id("run"), user_id=user.id, dataset_id=dataset.id, status="queued",
        progress_done=0, progress_total=dataset.case_count,
        config_snapshot_json=dump_json(snapshot_config),
        index_version_id=snapshot_config.get("index_version_id") or None,
        error_code=None, error_message=None, rescore_count=0,
        created_at=now, updated_at=now, finished_at=None,
    )
    db.add(run)
    db.flush()
    cases = db.scalars(select(EvalCase).where(EvalCase.dataset_id == dataset.id).order_by(EvalCase.seq)).all()
    for case in cases:
        db.add(EvalRunCase(run_id=run.id, case_id=case.id, question_snapshot=case.question,
                           answer="", citations_json="[]", human_note=""))
    db.commit()
    tasks.enqueue(db, user.id, "eval_run", {"run_id": run.id}, len(cases))
    return ok({"run_id": run.id, "status": "queued"})


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(EvalRun, run_id)
    if run is None or run.user_id != user.id:
        raise not_found("eval_run", run_id)
    return ok(eval_run_public(run))


@router.get("/runs/{run_id}/cases")
def cases(run_id: str, mode: str = Query(default="model"), filter: str = Query(default="all"),
          page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
          user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(EvalRun, run_id)
    if run is None or run.user_id != user.id:
        raise not_found("eval_run", run_id)
    if mode not in ("model", "human"):
        raise validation_error([{"field": "mode", "message": "只支持 model / human"}])
    if filter not in ("all", "scored", "unscored"):
        raise validation_error([{"field": "filter", "message": "不支持的筛选条件"}])
    base = select(EvalRunCase, EvalCase).join(EvalCase, EvalCase.id == EvalRunCase.case_id).where(
        EvalRunCase.run_id == run.id
    )
    if mode == "human":
        if filter == "scored":
            base = base.where(EvalRunCase.human_score.is_not(None))
        elif filter == "unscored":
            base = base.where(EvalRunCase.human_score.is_(None))
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = db.execute(base.order_by(EvalCase.seq).offset((page - 1) * page_size).limit(page_size)).all()
    items = [eval_case_model(case, row) if mode == "model" else eval_case_human(case, row) for row, case in rows]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.put("/runs/{run_id}/cases/{case_id}/human-score")
def human_score(run_id: str, case_id: str, payload: HumanScoreRequest,
                user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(EvalRun, run_id)
    if run is None or run.user_id != user.id:
        raise not_found("eval_run", run_id)
    row = db.get(EvalRunCase, (run_id, case_id))
    if row is None:
        raise not_found("eval_case", case_id)
    row.human_score = payload.score
    row.human_note = payload.note or ""
    row.human_scored_at = utcnow_iso() if payload.score is not None else None
    db.commit()
    return ok({"case_id": case_id, "human_score": row.human_score,
               "human_note": row.human_note, "human_scored_at": row.human_scored_at})


@router.post("/runs/{run_id}/rescore")
def rescore(request: Request, run_id: str, body: dict, user: User = Depends(get_current_user),
            settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
            tasks: TaskQueue = Depends(get_task_queue)) -> dict:
    _enforce_eval_limit(request, settings, user)
    run = db.get(EvalRun, run_id)
    if run is None or run.user_id != user.id:
        raise not_found("eval_run", run_id)
    if run.status not in ("succeeded", "failed"):
        raise ApiError(409, 40904, "当前运行状态不允许重新评分",
                       {"dataset_id": run.dataset_id, "running_run_id": run.id})
    judge_service_id = str(body.get("judge_service_id") or "")
    if not judge_service_id:
        raise validation_error([{"field": "judge_service_id", "message": "必填"}])
    judge = upstream.service_by_id(db, settings, user.id, judge_service_id)
    if judge is None or judge.type != "chat" or not judge.enabled:
        raise ApiError(409, 40903, "评分服务不可用", {"type": "judge"})
    snapshot = load_json(run.config_snapshot_json, {})
    snapshot["judge_service_id"] = judge.id or ""
    snapshot["judge_model"] = judge.model
    run.config_snapshot_json = dump_json(snapshot)
    run.status = "running"
    run.progress_done = 0
    run.rescore_count += 1
    run.error_code = run.error_message = None
    run.finished_at = None
    run.updated_at = utcnow_iso()
    db.execute(update(EvalRunCase).where(EvalRunCase.run_id == run.id)
               .values(model_score=None, model_reason=None, model_error=None))
    db.commit()
    tasks.enqueue(db, user.id, "eval_rescore", {"run_id": run.id}, run.progress_total)
    return ok({"run_id": run.id, "status": "running"})


@router.get("/runs/{run_id}/metrics")
def metrics(run_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    run = db.get(EvalRun, run_id)
    if run is None or run.user_id != user.id:
        raise not_found("eval_run", run_id)
    return ok(eval_service.compute_metrics(db, run))