"""SQLAlchemy models. All IDs are prefixed strings; timestamps are UTC ISO strings."""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .session import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_username_lower", func.lower(text("username")), unique=True),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ApiService(Base):
    __tablename__ = "api_services"
    __table_args__ = (
        Index("uq_service_name", "user_id", "type", func.lower(text("name")), unique=True),
        Index(
            "uq_service_default",
            "user_id",
            "type",
            unique=True,
            sqlite_where=text("is_default=1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    api_key_cipher: Mapped[str] = mapped_column(Text, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protocol: Mapped[str] = mapped_column(String(16), nullable=False, default="cohere")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ChangeLog(Base):
    __tablename__ = "change_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class UserSetting(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_user_status", "user_id", "deleted_at", "status", "updated_at"),
        Index("ix_documents_user_filename", "user_id", "filename"),
        Index("ix_documents_user_updated", "user_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    uploader: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    active_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reindex_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    purge_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    parse_meta_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_version_seq", "document_version_id", "seq"),
        Index("ix_chunks_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id"), nullable=False)
    document_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_versions.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph: Mapped[str | None] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)


class IndexVersion(Base):
    __tablename__ = "index_versions"
    __table_args__ = (
        Index("uq_index_active_user", "user_id", unique=True, sqlite_where=text("is_active=1")),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    embedding_service_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    activated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class IndexMember(Base):
    __tablename__ = "index_members"
    __table_args__ = (
        Index("ix_index_members_version", "index_version_id"),
        Index("ix_index_members_document", "document_id"),
    )

    index_version_id: Mapped[str] = mapped_column(String(64), ForeignKey("index_versions.id"), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(String(64), ForeignKey("chunks.id"), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_status_type", "status", "type"), Index("ix_tasks_created", "created_at"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class ReindexTaskItem(Base):
    __tablename__ = "reindex_task_items"

    task_id: Mapped[str] = mapped_column(String(64), ForeignKey("tasks.id"), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QaRecord(Base):
    __tablename__ = "qa_records"
    __table_args__ = (Index("ix_qa_records_user_created", "user_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    refused: Mapped[bool] = mapped_column(Boolean, nullable=False)
    refusal_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    citations_json: Mapped[str] = mapped_column(Text, nullable=False)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class EvalDataset(Base):
    __tablename__ = "eval_datasets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    answerable_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refusal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class EvalCase(Base):
    __tablename__ = "eval_cases"
    __table_args__ = (Index("ix_eval_cases_dataset_seq", "dataset_id", "seq"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String(64), ForeignKey("eval_datasets.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    reference_answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    gold_source: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    gold_refs_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    expected_refusal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, default="易")


class EvalRun(Base):
    __tablename__ = "eval_runs"
    __table_args__ = (Index("ix_eval_runs_user_dataset_status", "user_id", "dataset_id", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    progress_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    index_version_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescore_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    finished_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class EvalRunCase(Base):
    __tablename__ = "eval_run_cases"

    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("eval_runs.id"), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(64), ForeignKey("eval_cases.id"), primary_key=True)
    question_snapshot: Mapped[str] = mapped_column(Text, nullable=False, default="")
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    refused: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    refusal_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recall_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recall_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_valid: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    citation_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citation_valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    ndcg_score: Mapped[float | None] = mapped_column(nullable=True)
    human_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    human_scored_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)