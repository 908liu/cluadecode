"""ORM rows -> exact API response dictionaries."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.security import decrypt_key, mask_key
from ..core.utils import dump_json, load_json, plus_days_iso
from ..db.models import (
    ApiService,
    Chunk,
    Document,
    DocumentVersion,
    EvalCase,
    EvalDataset,
    EvalRun,
    EvalRunCase,
    ReindexTaskItem,
    Task,
)
from ..services import upstream


def service_public(settings: Settings, row: ApiService) -> dict:
    key = decrypt_key(settings, row.api_key_cipher)
    return {
        "id": row.id, "name": row.name, "type": row.type, "base_url": row.base_url,
        "model": row.model, "api_key_masked": mask_key(key), "timeout_seconds": row.timeout_seconds,
        "dimensions": row.dimensions, "protocol": getattr(row, "protocol", "cohere") or "cohere",
        "enabled": bool(row.enabled), "is_default": bool(row.is_default),
        "source": "db", "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _service_summary(service: upstream.ServiceInfo | None) -> dict | None:
    if service is None:
        return None
    return {"id": service.id, "name": service.name, "type": service.type,
            "model": service.model, "enabled": service.enabled,
            "protocol": service.protocol}


def system_status(db: Session, settings: Settings, user_id: str) -> dict:
    types: dict[str, dict] = {}
    for service_type in ("chat", "embedding", "rerank"):
        effective = upstream.effective_service(db, settings, user_id, service_type)
        env = upstream.env_service(settings, service_type)
        types[service_type] = {
            "configured": bool(effective and effective.enabled),
            "source": effective.source if effective else "none",
            "service": _service_summary(effective),
            "env": {"configured": env is not None, "service": _service_summary(env)},
        }
    available = int(db.scalar(
        select(func.count(Document.id)).where(
            Document.user_id == user_id, Document.status == "available", Document.deleted_at.is_(None)
        )
    ) or 0)
    reindex_required = bool(db.scalar(
        select(func.count(Document.id)).where(
            Document.user_id == user_id, Document.reindex_required.is_(True), Document.deleted_at.is_(None)
        )
    ))
    return {
        "ready": types["chat"]["configured"] and types["embedding"]["configured"],
        "available_documents": available,
        "reindex_required": reindex_required,
        "types": types,
    }


def _latest_version(db: Session, document_id: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
    )


def _queue_position(db: Session, doc: Document) -> int | None:
    if doc.status != "queued":
        return None
    tasks = db.scalars(
        select(Task).where(Task.user_id == doc.user_id, Task.type == "document_parse", Task.status == "queued")
        .order_by(Task.created_at)
    ).all()
    for index, task in enumerate(tasks, start=1):
        payload = load_json(task.payload_json, {})
        if payload.get("document_id") == doc.id:
            return index
    return None


def document_summary(db: Session, doc: Document) -> dict:
    version = _latest_version(db, doc.id)
    chunks = 0
    progress = 0
    error = None
    error_code = None
    if doc.status == "available":
        progress = 100
        active = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.active_version
            )
        )
        if active:
            chunks = int(db.scalar(select(func.count(Chunk.id)).where(Chunk.document_version_id == active.id)) or 0)
    elif doc.status == "failed":
        if version:
            progress = version.progress
            error = version.error or doc.error
            error_code = version.error_code or doc.error_code
        else:
            error = doc.error
            error_code = doc.error_code
    elif doc.status in ("queued", "parsing", "chunking", "indexing") and version:
        progress = version.progress
    purge_at = doc.purge_at
    if purge_at is None and doc.deleted_at:
        from datetime import datetime
        try:
            purge_at = plus_days_iso(7, datetime.fromisoformat(doc.deleted_at))
        except ValueError:
            purge_at = None
    return {
        "id": doc.id, "title": doc.title, "filename": doc.filename, "format": doc.format,
        "size_bytes": doc.size_bytes, "uploader": doc.uploader, "status": doc.status,
        "chunks": chunks, "error": error, "error_code": error_code,
        "active_version": doc.active_version, "reindex_required": bool(doc.reindex_required),
        "progress": progress, "queue_position": _queue_position(db, doc),
        "deleted_at": doc.deleted_at, "purge_at": purge_at,
        "created_at": doc.created_at, "updated_at": doc.updated_at,
    }


def document_detail(db: Session, doc: Document) -> dict:
    data = document_summary(db, doc)
    version = _latest_version(db, doc.id)
    data["parse_meta"] = load_json(version.parse_meta_json, None) if version and version.parse_meta_json else None
    return data


def reindex_task_public(db: Session, task: Task) -> dict:
    items = db.scalars(select(ReindexTaskItem).where(ReindexTaskItem.task_id == task.id)).all()
    done = sum(1 for item in items if item.status in ("succeeded", "failed", "skipped"))
    failed = sum(1 for item in items if item.status == "failed")
    status = task.status if task.status in ("queued", "running", "succeeded", "failed") else "failed"
    return {
        "task_id": task.id, "status": status, "total": task.progress_total or len(items),
        "done": done, "failed": failed,
        "accepted_document_ids": [item.document_id for item in items],
        "items": [{"document_id": item.document_id, "status": item.status,
                   "error_code": item.error_code, "error": item.error} for item in items],
        "started_at": task.started_at, "finished_at": task.finished_at,
        "error_message": task.error_message,
    }


def dataset_public(dataset: EvalDataset) -> dict:
    return {
        "id": dataset.id, "name": dataset.name, "description": dataset.description,
        "case_count": dataset.case_count, "answerable_count": dataset.answerable_count,
        "refusal_count": dataset.refusal_count, "version": dataset.version,
        "is_builtin": bool(dataset.is_builtin),
    }


def eval_run_public(run: EvalRun) -> dict:
    return {
        "id": run.id, "dataset_id": run.dataset_id, "status": run.status,
        "progress": {"done": run.progress_done, "total": run.progress_total},
        "config_snapshot": load_json(run.config_snapshot_json, {}),
        "error_code": run.error_code, "error_message": run.error_message,
        "rescore_count": run.rescore_count, "created_at": run.created_at,
        "updated_at": run.updated_at, "finished_at": run.finished_at,
    }


def _case_base(case: EvalCase, row: EvalRunCase) -> dict:
    return {
        "case_id": case.id, "question": row.question_snapshot or case.question,
        "reference_answer": case.reference_answer, "gold_source": case.gold_source,
        "expected_refusal": bool(case.expected_refusal), "tags": load_json(case.tags_json, []),
        "difficulty": case.difficulty, "answer": row.answer, "error": row.error,
        "recall_hit": bool(row.recall_hit) if row.recall_hit is not None else False,
        "ndcg_score": row.ndcg_score,
        "citation_valid": bool(row.citation_valid) if row.citation_valid is not None else False,
    }


def eval_case_model(case: EvalCase, row: EvalRunCase) -> dict:
    data = _case_base(case, row)
    data["model_score"] = row.model_score
    data["model_reason"] = row.model_reason
    return data


def eval_case_human(case: EvalCase, row: EvalRunCase) -> dict:
    data = _case_base(case, row)
    data["human_score"] = row.human_score
    data["human_note"] = row.human_note or ""
    data["human_scored_at"] = row.human_scored_at
    return data


def dump_snapshot(value: dict) -> str:
    return dump_json(value)