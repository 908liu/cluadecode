"""Per-user document lifecycle endpoints."""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ...core.config import Settings
from ...core.errors import ApiError, not_found, service_missing, state_error, validation_error
from ...core.utils import basename, dump_json, format_of, load_json, new_id, normalize_name, plus_days_iso, utcnow_iso
from ...db.models import (
    Chunk,
    Document,
    DocumentVersion,
    ReindexTaskItem,
    Task,
    User,
)
from ...db.session import Database
from ...services import files, upstream
from ...services.index_cache import IndexCacheRegistry
from ...services.task_queue import TaskQueue
from ..deps import (
    get_current_user,
    get_db,
    get_registry,
    get_settings,
    get_task_queue,
    rate_limit_error,
)
from ..responses import ok
from ..schemas import PreflightRequest, ReindexAllRequest
from ..serializers import document_detail, document_summary, reindex_task_public

router = APIRouter(prefix="/documents", tags=["documents"])
PROCESSING = ("queued", "parsing", "chunking", "indexing")
MEDIA_TYPES = {
    "PDF": "application/pdf",
    "Word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "Markdown": "text/markdown; charset=utf-8",
}


def _get_doc(db: Session, user_id: str, document_id: str) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user_id:
        raise not_found("document", document_id)
    return doc


def _latest_version(db: Session, document_id: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version.desc())
    )


def _active_version(db: Session, doc: Document) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.active_version
        )
    )


def _ensure_embedding(db: Session, settings: Settings, user_id: str) -> None:
    if upstream.effective_service(db, settings, user_id, "embedding") is None:
        raise service_missing("embedding")


def _enforce_upload_limit(request: Request, settings: Settings, user: User) -> None:
    limiter = request.app.state.upload_limiter
    retry = limiter.retry_after(user.id, settings.upload_rate_limit, settings.upload_rate_window)
    if retry is not None:
        raise rate_limit_error(retry)
    limiter.hit(user.id)


def _available_by_name(db: Session, user_id: str, filename: str) -> Document | None:
    target = normalize_name(filename)
    rows = db.scalars(
        select(Document).where(
            Document.user_id == user_id, Document.status == "available", Document.deleted_at.is_(None)
        )
    ).all()
    return next((row for row in rows if normalize_name(row.filename) == target), None)


def _available_by_sha(db: Session, user_id: str, sha256: str) -> tuple[Document, DocumentVersion] | None:
    row = db.execute(
        select(Document, DocumentVersion)
        .join(DocumentVersion, (DocumentVersion.document_id == Document.id)
              & (DocumentVersion.version == Document.active_version))
        .where(
            Document.user_id == user_id, Document.status == "available",
            Document.deleted_at.is_(None), DocumentVersion.sha256 == sha256,
        )
    ).first()
    return (row[0], row[1]) if row else None


def _existing(doc: Document) -> dict:
    return {
        "document_id": doc.id, "title": doc.title, "size_bytes": doc.size_bytes,
        "uploader": doc.uploader, "active_version": doc.active_version, "updated_at": doc.updated_at,
    }


def _conflict(conflict: str, existing: Document | None) -> ApiError:
    actions = {
        "duplicate": ["keep_both", "cancel"],
        "same_name": ["overwrite", "keep_both", "cancel"],
        "both": ["keep_both", "cancel"],
    }[conflict]
    return ApiError(409, 40901, "文件重复或同名", {
        "conflict": conflict, "existing_document_id": existing.id if existing else None,
        "allowed_actions": actions,
    })


def _keep_both_name(db: Session, user_id: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    existing = {
        normalize_name(row) for row in db.scalars(
            select(Document.filename).where(Document.user_id == user_id, Document.deleted_at.is_(None))
        ).all()
    }
    for index in range(2, 102):
        candidate = f"{stem} ({index}){ext}"
        if normalize_name(candidate) not in existing:
            return candidate
    raise _conflict("same_name", None)


def _normalize_uploader(value: str | None, user: User) -> str:
    uploader = (value or "").strip()
    if len(uploader) > 32:
        raise validation_error([{"field": "uploader_display", "message": "不能超过 32 个字符"}])
    return uploader or (user.display_name or "").strip() or "匿名"


@router.get("")
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default=""),
    status: str = Query(default="all"),
    format: str = Query(default="all"),
    sort: str = Query(default="-updated_at"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if status not in ("all", "available", "processing", "failed", "trash"):
        raise validation_error([{"field": "status", "message": "不支持的筛选状态"}])
    if format not in ("all", "PDF", "Word", "Markdown"):
        raise validation_error([{"field": "format", "message": "不支持的格式"}])
    sort_field = sort[1:] if sort.startswith("-") else sort
    if sort_field not in ("updated_at", "created_at", "title"):
        raise validation_error([{"field": "sort", "message": "不支持的排序字段"}])
    if len(q) > 100:
        raise validation_error([{"field": "q", "message": "搜索词不能超过 100 个字符"}])
    query = select(Document).where(Document.user_id == user.id)
    if status == "all":
        query = query.where(Document.deleted_at.is_(None))
    elif status == "available":
        query = query.where(Document.status == "available", Document.deleted_at.is_(None))
    elif status == "processing":
        query = query.where(Document.status.in_(PROCESSING), Document.deleted_at.is_(None))
    elif status == "failed":
        query = query.where(Document.status == "failed", Document.deleted_at.is_(None))
    else:
        query = query.where(Document.deleted_at.is_not(None))
    if format != "all":
        query = query.where(Document.format == format)
    if q.strip():
        query = query.where(Document.title.ilike(f"%{q.strip()}%"))
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    order_column = func.lower(Document.title) if sort_field == "title" else getattr(Document, sort_field)
    query = query.order_by(order_column.desc() if sort.startswith("-") else order_column.asc())
    rows = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return ok({"items": [document_summary(db, row) for row in rows], "total": total, "page": page, "page_size": page_size})


@router.get("/status")
def document_statuses(ids: str = Query(...), user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)) -> dict:
    seen: list[str] = []
    for item in ids.split(","):
        item = item.strip()
        if item and item not in seen:
            seen.append(item)
    if not seen or len(seen) > 50:
        raise validation_error([{"field": "ids", "message": "ids 必须为 1~50 个"}])
    items = []
    for document_id in seen:
        doc = db.get(Document, document_id)
        if doc is None or doc.user_id != user.id:
            items.append({"id": document_id, "status": "missing", "chunks": 0, "progress": 0,
                          "queue_position": None, "error": None, "error_code": None, "updated_at": utcnow_iso()})
            continue
        summary = document_summary(db, doc)
        items.append({"id": doc.id, "status": doc.status, "chunks": summary["chunks"],
                      "progress": summary["progress"], "queue_position": summary["queue_position"],
                      "error": summary["error"], "error_code": summary["error_code"], "updated_at": doc.updated_at})
    return ok({"items": items})


@router.get("/reindex/status")
def reindex_status(task_id: str = Query(...), user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)) -> dict:
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id or task.type != "reindex_all":
        raise not_found("task", task_id)
    return ok(reindex_task_public(db, task))


@router.post("/preflight")
def preflight(payload: PreflightRequest, user: User = Depends(get_current_user),
              settings: Settings = Depends(get_settings), db: Session = Depends(get_db)) -> dict:
    client_ids = [item.client_id for item in payload.files]
    if len(set(client_ids)) != len(client_ids):
        raise validation_error([{"field": "files.client_id", "message": "client_id 必须唯一"}])
    results = []
    for item in payload.files:
        name = basename(item.name)
        same_name = _available_by_name(db, user.id, name)
        duplicate = None
        if item.sha256:
            found = _available_by_sha(db, user.id, item.sha256)
            duplicate = found[0] if found else None
        status, message = "ok", None
        if not name or len(name) > 255:
            status, message = "invalid", "文件名非法"
        elif item.size_bytes > settings.upload_max_bytes:
            status, message = "too_large", "文件超过 50MB 限制"
        elif format_of(name) is None:
            status, message = "unsupported", "仅支持 PDF、Word、Markdown"
        elif same_name and duplicate:
            status, message = "duplicate_and_same_name", "内容重复且文件名相同"
        elif duplicate:
            status, message = "duplicate", "内容重复"
        elif same_name:
            status, message = "same_name", "已存在同名文档"
        allowed = {
            "ok": [], "duplicate": ["keep_both", "cancel"],
            "same_name": ["overwrite", "keep_both", "cancel"],
            "duplicate_and_same_name": ["keep_both", "cancel"],
            "unsupported": ["cancel"], "too_large": ["cancel"], "invalid": ["cancel"],
        }[status]
        results.append({
            "client_id": item.client_id, "name": name, "status": status,
            "existing": _existing(same_name) if same_name else None,
            "duplicate_of": _existing(duplicate) if duplicate else None,
            "allowed_actions": allowed, "message": message,
        })
    duplicate_groups: dict[str, list[str]] = {}
    name_groups: dict[str, list[str]] = {}
    for item in payload.files:
        if item.sha256:
            duplicate_groups.setdefault(item.sha256, []).append(item.client_id)
        name_groups.setdefault(normalize_name(basename(item.name)), []).append(item.client_id)
    batch = {
        "duplicate_groups": [group for group in duplicate_groups.values() if len(group) > 1],
        "same_name_groups": [group for group in name_groups.values() if len(group) > 1],
    }
    data: dict = {"results": results}
    if batch["duplicate_groups"] or batch["same_name_groups"]:
        data["batch_conflicts"] = batch
    return ok(data)


@router.post("/upload")
def upload(
    request: Request,
    file: UploadFile = File(...),
    conflict_action: str = Form(...),
    uploader_display: str | None = Form(default=None),
    existing_document_id: str | None = Form(default=None),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_db),
    tasks: TaskQueue = Depends(get_task_queue),
):
    if conflict_action not in ("none", "overwrite", "keep_both"):
        raise validation_error([{"field": "conflict_action", "message": "不支持的冲突处理方式"}])
    _enforce_upload_limit(request, settings, user)
    _ensure_embedding(db, settings, user.id)
    original = basename(file.filename or "")
    if not original:
        raise validation_error([{"field": "file", "message": "缺少文件名"}])
    fmt = format_of(original)
    if fmt is None:
        raise ApiError(415, 41501, "文件格式不支持", {"allowed_formats": ["PDF", "Word", "Markdown"]})
    uploader = _normalize_uploader(uploader_display, user)
    target: Document | None = None
    version_number = 1
    if conflict_action == "overwrite":
        if not existing_document_id:
            raise validation_error([{"field": "existing_document_id", "message": "覆盖时必须提供"}])
        target = db.get(Document, existing_document_id)
        if target is None or target.user_id != user.id or target.deleted_at is not None:
            raise not_found("document", existing_document_id)
        if target.status not in ("available", "failed"):
            raise state_error(target.id, target.status, "overwrite")
        if normalize_name(target.filename) != normalize_name(original):
            raise ApiError(409, 40901, "覆盖目标文件名不一致", {
                "conflict": "same_name", "existing_document_id": target.id,
                "allowed_actions": ["overwrite", "keep_both", "cancel"],
            })
        latest = _latest_version(db, target.id)
        version_number = (latest.version + 1) if latest else 1
    document_id = target.id if target else new_id("d")
    path = ""
    try:
        try:
            path, size, sha256 = files.save_upload(file.file, settings, document_id, version_number, original)
        except files.UploadTooLarge as exc:
            raise ApiError(413, 41301, "文件超过大小限制", {
                "max_bytes": settings.upload_max_bytes, "actual_bytes": settings.upload_max_bytes,
            }) from exc
        except files.UploadEmpty as exc:
            raise ApiError(400, 40001, "文件不能为空", None) from exc
        if not files.magic_ok(path, fmt) or (fmt == "Word" and not files.validate_docx_zip(path, settings.docx_max_uncompressed_bytes)):
            raise ApiError(415, 41501, "文件内容与扩展名不匹配", {"allowed_formats": ["PDF", "Word", "Markdown"]})
        same_name = _available_by_name(db, user.id, original)
        duplicate_found = _available_by_sha(db, user.id, sha256)
        duplicate = duplicate_found[0] if duplicate_found else None
        if conflict_action == "none":
            if same_name and duplicate:
                raise _conflict("both", same_name)
            if duplicate:
                raise _conflict("duplicate", duplicate)
            if same_name:
                raise _conflict("same_name", same_name)
        storable = original
        if conflict_action == "keep_both":
            storable = _keep_both_name(db, user.id, original)
        elif target is not None:
            storable = target.filename
        now = utcnow_iso()
        if target is not None:
            doc = target
            doc.filename, doc.title, doc.size_bytes, doc.format = storable, storable, size, fmt
            doc.uploader, doc.status = uploader, "queued"
            doc.error, doc.error_code, doc.updated_at = None, None, now
        else:
            doc = Document(
                id=document_id, user_id=user.id, title=storable, filename=storable, format=fmt,
                size_bytes=size, uploader=uploader, status="queued", active_version=version_number,
                reindex_required=False, error=None, error_code=None, deleted_at=None, purge_at=None,
                created_at=now, updated_at=now,
            )
            db.add(doc)
        version = DocumentVersion(
            id=new_id("v"), document_id=doc.id, version=version_number, file_path=path,
            sha256=sha256, size_bytes=size, status="queued", progress=0, created_at=now, updated_at=now,
        )
        db.add(version)
        db.flush()
        tasks.enqueue(db, user.id, "document_parse", {"document_id": doc.id, "version_id": version.id}, 100)
        return ok(document_summary(db, doc))
    except Exception:
        if path:
            files.delete_file(path)
        raise


@router.get("/{document_id}")
def get_document(document_id: str, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    return ok(document_detail(db, doc))


@router.get("/{document_id}/chunks")
def get_chunks(document_id: str, page: int = Query(default=1, ge=1),
               page_size: int = Query(default=20, ge=1, le=100),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.status != "available":
        raise ApiError(409, 40905, "文档尚未完成处理", {"document_id": doc.id, "status": doc.status})
    version = _active_version(db, doc)
    if version is None:
        raise not_found("document_version", document_id)
    base = select(Chunk).where(Chunk.document_version_id == version.id)
    total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = db.scalars(base.order_by(Chunk.seq).offset((page - 1) * page_size).limit(page_size)).all()
    return ok({"items": [
        {"id": row.id, "seq": row.seq, "page": row.page, "paragraph": row.paragraph, "text": row.text}
        for row in rows
    ], "total": total, "page": page, "page_size": page_size})


@router.get("/{document_id}/download")
def download_document(document_id: str, version: str = Query(default="active"),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doc = _get_doc(db, user.id, document_id)
    if version == "active":
        number = doc.active_version
    elif version == "previous":
        number = doc.active_version - 1
        if number <= 0:
            raise ApiError(409, 40907, "指定版本不存在", {"document_id": doc.id, "version": "previous"})
    elif version.isdigit():
        number = int(version)
    else:
        raise validation_error([{"field": "version", "message": "版本参数非法"}])
    row = db.scalar(select(DocumentVersion).where(
        DocumentVersion.document_id == doc.id, DocumentVersion.version == number
    ))
    if row is None:
        raise ApiError(409, 40907, "指定版本不存在", {"document_id": doc.id, "version": number})
    if not Path(row.file_path).exists():
        raise not_found("file", row.file_path)
    return FileResponse(row.file_path, media_type=MEDIA_TYPES.get(doc.format, "application/octet-stream"),
                        filename=doc.filename)


@router.post("/{document_id}/retry")
def retry(request: Request, document_id: str, user: User = Depends(get_current_user),
          settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
          tasks: TaskQueue = Depends(get_task_queue)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.status != "failed":
        raise state_error(doc.id, doc.status, "retry")
    _enforce_upload_limit(request, settings, user)
    _ensure_embedding(db, settings, user.id)
    version = _latest_version(db, doc.id)
    if version is None:
        raise not_found("document_version", document_id)
    version.status, version.progress, version.error, version.error_code = "queued", 0, None, None
    version.updated_at = utcnow_iso()
    doc.status, doc.error, doc.error_code, doc.updated_at = "queued", None, None, utcnow_iso()
    db.commit()
    tasks.enqueue(db, user.id, "document_parse", {"document_id": doc.id, "version_id": version.id}, 100)
    return ok(document_summary(db, doc))


@router.post("/{document_id}/reindex")
def reindex(request: Request, document_id: str, user: User = Depends(get_current_user),
            settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
            tasks: TaskQueue = Depends(get_task_queue)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.status not in ("available", "failed"):
        raise state_error(doc.id, doc.status, "reindex")
    _enforce_upload_limit(request, settings, user)
    _ensure_embedding(db, settings, user.id)
    version = _latest_version(db, doc.id)
    doc.status, doc.updated_at = "indexing", utcnow_iso()
    if version is not None:
        version.progress, version.updated_at = 10, utcnow_iso()
    db.commit()
    tasks.enqueue(db, user.id, "document_reindex", {"document_id": doc.id}, 100)
    return ok(document_summary(db, doc))


@router.post("/reindex-all")
def reindex_all(request: Request, payload: ReindexAllRequest, user: User = Depends(get_current_user),
                settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
                tasks: TaskQueue = Depends(get_task_queue)) -> dict:
    if payload.document_ids is not None:
        raise validation_error([{"field": "document_ids", "message": "v1 只支持全量重建，不能传 document_ids"}])
    _enforce_upload_limit(request, settings, user)
    _ensure_embedding(db, settings, user.id)
    docs = db.scalars(select(Document).where(
        Document.user_id == user.id, Document.status.in_(["available", "failed"]),
        Document.deleted_at.is_(None),
    )).all()
    if not docs:
        raise validation_error([{"field": "document_ids", "message": "没有可重建的文档"}])
    task = Task(
        id=new_id("task"), user_id=user.id, type="reindex_all", status="queued",
        progress_done=0, progress_total=len(docs), payload_json=dump_json({}),
        attempts=0, created_at=utcnow_iso(),
    )
    db.add(task)
    db.flush()
    for doc in docs:
        db.add(ReindexTaskItem(task_id=task.id, document_id=doc.id, status="queued"))
    db.commit()
    tasks.submit(task.id)
    return ok({"task_id": task.id, "accepted_document_ids": [doc.id for doc in docs], "rejected": []})


@router.delete("/{document_id}")
def delete_document(document_id: str, user: User = Depends(get_current_user),
                    settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
                    registry: IndexCacheRegistry = Depends(get_registry)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.status in PROCESSING:
        raise state_error(doc.id, doc.status, "delete")
    if doc.deleted_at is not None:
        return ok({"id": doc.id, "status": doc.status, "deleted_at": doc.deleted_at, "purge_at": doc.purge_at})
    now = utcnow_iso()
    doc.status, doc.deleted_at, doc.purge_at, doc.updated_at = "deleted", now, plus_days_iso(settings.trash_retention_days), now
    db.commit()
    registry.rebuild(db, user.id)
    return ok({"id": doc.id, "status": doc.status, "deleted_at": doc.deleted_at, "purge_at": doc.purge_at})


@router.post("/{document_id}/restore")
def restore(request: Request, document_id: str, user: User = Depends(get_current_user),
            settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
            tasks: TaskQueue = Depends(get_task_queue), registry: IndexCacheRegistry = Depends(get_registry)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.deleted_at is None:
        raise state_error(doc.id, doc.status, "restore")
    _enforce_upload_limit(request, settings, user)
    _ensure_embedding(db, settings, user.id)
    version = _latest_version(db, doc.id)
    if version is None or not Path(version.file_path).exists():
        raise ApiError(409, 40907, "指定版本或文件不存在", {"document_id": doc.id, "version": doc.active_version})
    now = utcnow_iso()
    doc.deleted_at = doc.purge_at = None
    doc.status, doc.error, doc.error_code, doc.updated_at = "queued", None, None, now
    version.status, version.progress, version.error, version.error_code, version.updated_at = "queued", 0, None, None, now
    db.commit()
    tasks.enqueue(db, user.id, "document_parse", {"document_id": doc.id, "version_id": version.id}, 100)
    registry.rebuild(db, user.id)
    return ok(document_summary(db, doc))


@router.delete("/{document_id}/purge")
def purge(document_id: str, user: User = Depends(get_current_user),
          settings: Settings = Depends(get_settings), db: Session = Depends(get_db),
          registry: IndexCacheRegistry = Depends(get_registry)) -> dict:
    doc = _get_doc(db, user.id, document_id)
    if doc.deleted_at is None:
        raise state_error(doc.id, doc.status, "purge")
    registry.delete_document_everywhere(db, doc.id)
    db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == doc.id))
    db.execute(delete(ReindexTaskItem).where(ReindexTaskItem.document_id == doc.id))
    db.execute(delete(Task).where(Task.payload_json.like(f'%"{doc.id}"%')))
    db.delete(doc)
    db.commit()
    files.delete_document_files(settings, document_id)
    registry.rebuild(db, user.id)
    return ok({"id": document_id})