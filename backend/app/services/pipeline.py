"""Document parsing, chunking, embedding, indexing and reindex pipelines."""
from __future__ import annotations

import importlib.metadata
import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.utils import dump_json, new_id, utcnow_iso
from ..db.models import (
    Chunk,
    Document,
    DocumentVersion,
    IndexMember,
    IndexVersion,
    ReindexTaskItem,
    Task,
)
from . import upstream
from .index_cache import IndexCacheRegistry, IndexWriteError


class PipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _parser_version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def parse_pdf(path: str, settings: Settings) -> tuple[list[dict], dict]:
    from pypdf import PdfReader

    reader = PdfReader(path)
    if len(reader.pages) > settings.pdf_max_pages:
        raise PipelineError("too_large", f"PDF 页数超过上限 {settings.pdf_max_pages}")
    blocks: list[dict] = []
    warnings: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if not text.strip():
            warnings.append(f"第 {index} 页无可提取文字")
        blocks.append({"text": text, "page": index, "paragraph": None})
    char_count = sum(len(block["text"]) for block in blocks)
    if char_count > settings.doc_max_chars:
        raise PipelineError("too_large", f"提取字符数超过上限 {settings.doc_max_chars}")
    if char_count == 0:
        raise PipelineError("empty_text", "文档没有可提取的文字")
    return blocks, {
        "page_count": len(reader.pages), "paragraph_count": None, "char_count": char_count,
        "parser": "pypdf", "parser_version": _parser_version("pypdf"), "warnings": warnings,
    }


def parse_docx(path: str, settings: Settings) -> tuple[list[dict], dict]:
    from docx import Document as DocxDocument

    document = DocxDocument(path)
    blocks: list[dict] = []
    paragraph_index = 0
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            paragraph_index += 1
            blocks.append({"text": text, "page": None, "paragraph": f"§{paragraph_index}"})
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                paragraph_index += 1
                blocks.append({"text": " | ".join(cells), "page": None, "paragraph": f"§{paragraph_index}"})
    char_count = sum(len(block["text"]) for block in blocks)
    if char_count > settings.doc_max_chars:
        raise PipelineError("too_large", f"提取字符数超过上限 {settings.doc_max_chars}")
    if char_count == 0:
        raise PipelineError("empty_text", "文档没有可提取的文字")
    return blocks, {
        "page_count": None, "paragraph_count": paragraph_index, "char_count": char_count,
        "parser": "python-docx", "parser_version": _parser_version("python-docx"), "warnings": [],
    }


def parse_markdown(path: str, settings: Settings) -> tuple[list[dict], dict]:
    raw = Path(path).read_bytes().decode("utf-8", errors="replace")
    blocks: list[dict] = []
    paragraph_index = 0
    buffer: list[str] = []
    for line in raw.splitlines():
        if line.strip():
            buffer.append(line.rstrip())
        elif buffer:
            paragraph_index += 1
            blocks.append({"text": "\n".join(buffer), "page": None, "paragraph": f"§{paragraph_index}"})
            buffer = []
    if buffer:
        paragraph_index += 1
        blocks.append({"text": "\n".join(buffer), "page": None, "paragraph": f"§{paragraph_index}"})
    char_count = sum(len(block["text"]) for block in blocks)
    if char_count > settings.doc_max_chars:
        raise PipelineError("too_large", f"提取字符数超过上限 {settings.doc_max_chars}")
    if not blocks:
        raise PipelineError("empty_text", "文档没有可提取的文字")
    return blocks, {
        "page_count": None, "paragraph_count": paragraph_index, "char_count": char_count,
        "parser": "markdown", "parser_version": "1", "warnings": [],
    }


def parse_file(path: str, fmt: str, settings: Settings) -> tuple[list[dict], dict]:
    try:
        if fmt == "PDF":
            return parse_pdf(path, settings)
        if fmt == "Word":
            return parse_docx(path, settings)
        return parse_markdown(path, settings)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("parse_failed", f"文档解析失败：{exc}") from exc


def chunk_blocks(blocks: list[dict], settings: Settings) -> list[dict]:
    size = max(100, settings.chunk_size)
    overlap = min(max(0, settings.chunk_overlap), size // 2)
    chunks: list[dict] = []
    current = ""
    current_page = None
    current_paragraph = None

    def flush() -> None:
        nonlocal current, current_page, current_paragraph
        text = current.strip()
        if text:
            chunks.append({"text": text, "page": current_page, "paragraph": current_paragraph})
        current = ""

    for block in blocks:
        text = re.sub(r"[ \t]+", " ", block.get("text") or "").strip()
        if not text:
            continue
        page, paragraph = block.get("page"), block.get("paragraph")
        if len(text) > size:
            flush()
            start = 0
            while start < len(text):
                piece = text[start : start + size].strip()
                if piece:
                    chunks.append({"text": piece, "page": page, "paragraph": paragraph})
                if start + size >= len(text):
                    break
                start += max(1, size - overlap)
            continue
        if not current:
            current, current_page, current_paragraph = text, page, paragraph
        elif len(current) + len(text) + 1 <= size:
            current = f"{current}\n{text}"
        else:
            flush()
            current, current_page, current_paragraph = text, page, paragraph
    flush()
    if not chunks:
        raise PipelineError("chunk_failed", "文档没有可切分的文字")
    return chunks


def _set_version(db: Session, doc: Document, version: DocumentVersion, status: str, progress: int) -> None:
    doc.status = status
    doc.updated_at = utcnow_iso()
    version.status = status
    version.progress = progress
    version.updated_at = utcnow_iso()
    db.commit()


def _fail(db: Session, doc: Document, version: DocumentVersion, code: str, message: str) -> None:
    version.status = "failed"
    version.error_code = code
    version.error = message
    version.updated_at = utcnow_iso()
    old_available = False
    if doc.active_version and doc.active_version != version.version:
        old = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.active_version
            )
        )
        old_available = bool(old and old.status == "available")
    doc.status = "failed"
    doc.error = message
    doc.error_code = code
    doc.reindex_required = bool(old_available)
    doc.updated_at = utcnow_iso()
    db.commit()


def _active_index(db: Session, user_id: str) -> IndexVersion | None:
    return db.scalar(
        select(IndexVersion).where(IndexVersion.user_id == user_id, IndexVersion.is_active.is_(True))
    )


def _embedding_for_index(db: Session, settings: Settings, user_id: str, index: IndexVersion) -> upstream.ServiceInfo:
    service = (
        upstream.service_by_id(db, settings, user_id, index.embedding_service_id)
        if index.embedding_service_id
        else upstream.env_service(settings, "embedding")
    )
    if service is None or not service.enabled:
        raise PipelineError("embedding_failed", "索引绑定的 Embedding 服务不可用")
    if service.model != index.model:
        raise PipelineError("embedding_failed", "索引绑定的 Embedding 模型已变化")
    if service.dimensions is not None and service.dimensions != index.dimensions:
        raise PipelineError("embedding_failed", "索引绑定的 Embedding 维度已变化")
    return service


def _activate_index(db: Session, index: IndexVersion) -> None:
    for row in db.scalars(select(IndexVersion).where(IndexVersion.user_id == index.user_id, IndexVersion.is_active.is_(True))).all():
        row.is_active = False
        row.status = "retired"
    # Flush the deactivation before activating the new row; SQLite's partial
    # unique index would otherwise reject the intermediate active state.
    db.flush()
    index.is_active = True
    index.status = "active"
    index.activated_at = utcnow_iso()
    db.commit()


def _embed(service: upstream.ServiceInfo, texts: list[str], batch_size: int = 32) -> list[list[float]]:
    vectors: list[list[float]] = []
    batch_size = max(1, batch_size)
    for start in range(0, len(texts), batch_size):
        try:
            vectors.extend(upstream.embed_texts(service, texts[start : start + batch_size]))
        except upstream.UpstreamError as exc:
            raise PipelineError("embedding_failed", exc.message) from exc
    if len(vectors) != len(texts):
        raise PipelineError("embedding_failed", "Embedding 返回数量与切片数量不一致")
    return vectors




def process_document(
    db: Session, settings: Settings, registry: IndexCacheRegistry,
    user_id: str, document_id: str, version_id: str,
) -> None:
    doc = db.get(Document, document_id)
    version = db.get(DocumentVersion, version_id)
    if doc is None or version is None or doc.user_id != user_id:
        return
    _set_version(db, doc, version, "parsing", 10)
    try:
        blocks, meta = parse_file(version.file_path, doc.format, settings)
        version.parse_meta_json = dump_json(meta)
        _set_version(db, doc, version, "chunking", 40)
        chunks_data = chunk_blocks(blocks, settings)
        old_ids = db.scalars(
            select(Chunk.id).where(Chunk.document_version_id == version.id)
        ).all()
        if old_ids:
            db.execute(delete(IndexMember).where(IndexMember.chunk_id.in_(old_ids)))
        db.execute(delete(Chunk).where(Chunk.document_version_id == version.id))
        chunks: list[Chunk] = []
        for seq, item in enumerate(chunks_data, start=1):
            chunk = Chunk(
                id=new_id("c"), document_id=doc.id, document_version_id=version.id,
                seq=seq, page=item["page"], paragraph=item["paragraph"],
                text=item["text"], char_count=len(item["text"]),
            )
            chunks.append(chunk)
            db.add(chunk)
        db.flush()
        _set_version(db, doc, version, "indexing", 70)
        active = _active_index(db, user_id)
        if active is not None:
            service = _embedding_for_index(db, settings, user_id, active)
            index = active
        else:
            service = upstream.effective_service(db, settings, user_id, "embedding")
            if service is None or service.dimensions is None:
                raise PipelineError("embedding_failed", "Embedding 服务未配置")
            index = IndexVersion(
                id=new_id("idx"), user_id=user_id, embedding_service_id=service.id,
                model=service.model, dimensions=service.dimensions, status="building",
                is_active=False, created_at=utcnow_iso(),
            )
            db.add(index)
            db.flush()
        vectors = _embed(service, [chunk.text for chunk in chunks], settings.embedding_batch_size)
        registry.replace_document(db, index, doc.id, chunks, vectors)
        version.status = "available"
        version.progress = 100
        version.error = None
        version.error_code = None
        version.updated_at = utcnow_iso()
        doc.status = "available"
        doc.active_version = version.version
        doc.reindex_required = False
        doc.error = None
        doc.error_code = None
        doc.updated_at = utcnow_iso()
        db.commit()
        if not index.is_active:
            _activate_index(db, index)
        registry.rebuild(db, user_id)
        registry.cleanup_retired(db, user_id)
    except PipelineError as exc:
        db.rollback()
        _fail(db, doc, version, exc.code, exc.message)
    except IndexWriteError as exc:
        db.rollback()
        _fail(db, doc, version, "index_failed", str(exc))
    except Exception as exc:
        db.rollback()
        _fail(db, doc, version, "internal", f"处理失败：{exc}")


def reindex_document(
    db: Session, settings: Settings, registry: IndexCacheRegistry, user_id: str, document_id: str
) -> None:
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user_id:
        return
    version = db.scalar(
        select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version.desc())
    )
    if version is None:
        return
    active = _active_index(db, user_id)
    chunks = db.scalars(
        select(Chunk).where(Chunk.document_version_id == version.id).order_by(Chunk.seq)
    ).all() if version else []
    if active is None or not chunks:
        process_document(db, settings, registry, user_id, doc.id, version.id)
        return
    doc.status = "indexing"
    doc.updated_at = utcnow_iso()
    version.progress = 10
    version.updated_at = utcnow_iso()
    db.commit()
    try:
        service = _embedding_for_index(db, settings, user_id, active)
        vectors = _embed(service, [chunk.text for chunk in chunks], settings.embedding_batch_size)
        registry.replace_document(db, active, doc.id, list(chunks), vectors)
        doc.status = "available"
        doc.active_version = version.version
        doc.reindex_required = False
        doc.error = None
        doc.error_code = None
        doc.updated_at = utcnow_iso()
        version.status = "available"
        version.progress = 100
        version.updated_at = utcnow_iso()
        db.commit()
        registry.rebuild(db, user_id)
    except PipelineError as exc:
        db.rollback()
        _fail(db, doc, version, exc.code, exc.message)
    except IndexWriteError as exc:
        db.rollback()
        _fail(db, doc, version, "index_failed", str(exc))
    except Exception as exc:
        db.rollback()
        _fail(db, doc, version, "internal", f"重建索引失败：{exc}")


def reindex_all(
    db: Session, settings: Settings, registry: IndexCacheRegistry, task: Task
) -> None:
    items = db.scalars(select(ReindexTaskItem).where(ReindexTaskItem.task_id == task.id)).all()
    if not items:
        task.status = "failed"
        task.error_code = "not_reindexable"
        task.error_message = "没有可重建的文档"
        task.finished_at = utcnow_iso()
        db.commit()
        return
    service = upstream.effective_service(db, settings, task.user_id, "embedding")
    if service is None or service.dimensions is None:
        task.status = "failed"
        task.error_code = "embedding_failed"
        task.error_message = "Embedding 服务未配置"
        task.finished_at = utcnow_iso()
        db.commit()
        return
    new_index = IndexVersion(
        id=new_id("idx"), user_id=task.user_id, embedding_service_id=service.id,
        model=service.model, dimensions=service.dimensions, status="building",
        is_active=False, created_at=utcnow_iso(),
    )
    db.add(new_index)
    db.commit()
    task.status = "running"
    task.started_at = task.started_at or utcnow_iso()
    task.progress_total = len(items)
    db.commit()
    succeeded = failed = skipped = 0
    successful_docs: list[str] = []
    originals: dict[str, tuple[str, str | None, str | None]] = {}
    for position, item in enumerate(items, start=1):
        doc = db.get(Document, item.document_id)
        if doc is None or doc.user_id != task.user_id or doc.deleted_at is not None:
            item.status = "skipped"
            item.error_code = "deleted"
            item.error = "文档不存在或已删除"
            skipped += 1
            task.progress_done = position
            db.commit()
            continue
        originals[doc.id] = (doc.status, doc.error, doc.error_code)
        if doc.status not in ("available", "failed"):
            item.status = "skipped"
            item.error_code = "not_reindexable"
            item.error = "当前文档状态不可重建"
            doc.reindex_required = True
            skipped += 1
            task.progress_done = position
            db.commit()
            continue
        version = db.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == doc.id, DocumentVersion.version == doc.active_version
            )
        )
        chunks = db.scalars(
            select(Chunk).where(Chunk.document_version_id == version.id).order_by(Chunk.seq)
        ).all() if version else []
        if not chunks:
            item.status = "failed"
            item.error_code = "chunk_failed"
            item.error = "文档没有可重建索引的切片"
            doc.reindex_required = True
            failed += 1
            task.progress_done = position
            db.commit()
            continue
        item.status = "indexing"
        db.commit()
        try:
            vectors = _embed(service, [chunk.text for chunk in chunks], settings.embedding_batch_size)
            registry.replace_document(db, new_index, doc.id, list(chunks), vectors)
            item.status = "succeeded"
            item.error = None
            item.error_code = None
            doc.status = "available"
            doc.reindex_required = False
            doc.error = None
            doc.error_code = None
            doc.updated_at = utcnow_iso()
            succeeded += 1
            successful_docs.append(doc.id)
        except (PipelineError, IndexWriteError) as exc:
            db.rollback()
            item.status = "failed"
            item.error_code = exc.code
            item.error = exc.message
            if doc:
                doc.status = "failed"
                doc.reindex_required = True
                doc.error_code = exc.code
                doc.error = exc.message
                doc.updated_at = utcnow_iso()
            failed += 1
        task.progress_done = position
        db.commit()
    if succeeded > 0:
        _activate_index(db, new_index)
        task.status = "succeeded" if failed == 0 else "failed"
        task.error_code = None if failed == 0 else "partial_failed"
        task.error_message = None if failed == 0 else f"{failed} 个文档重建失败"
        current = upstream.effective_service(db, settings, task.user_id, "embedding")
        if current is None or current.model != new_index.model or current.dimensions != new_index.dimensions:
            for doc_id in successful_docs:
                doc = db.get(Document, doc_id)
                if doc:
                    doc.reindex_required = True
        db.commit()
        registry.cleanup_retired(db, task.user_id)
    else:
        new_index.status = "failed"
        task.status = "failed"
        task.error_code = "reindex_failed"
        task.error_message = "全部文档重建失败，旧索引保持不变"
        for doc_id, (status, error, error_code) in originals.items():
            doc = db.get(Document, doc_id)
            if doc:
                doc.status = status
                doc.error = error
                doc.error_code = error_code
                doc.reindex_required = True
                doc.updated_at = utcnow_iso()
        registry.drop_index(db, new_index.id)
        db.commit()
    task.result_json = dump_json({"succeeded": succeeded, "failed": failed, "skipped": skipped})
    task.finished_at = utcnow_iso()
    db.commit()
    registry.rebuild(db, task.user_id)