"""Environment-backed settings. No module-level database or queue globals."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _secret(data_dir: Path) -> str:
    value = os.getenv("APP_SECRET")
    if value:
        return value
    path = data_dir / ".app_secret"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    generated = secrets.token_urlsafe(48)
    path.write_text(generated, encoding="utf-8")
    return generated


@dataclass(frozen=True)
class Settings:
    app_env: str
    backend_dir: Path
    data_dir: Path
    database_path: Path
    chroma_dir: Path
    app_secret: str
    encryption_key_path: Path
    token_ttl_seconds: int
    upload_max_bytes: int
    chunk_size: int
    chunk_overlap: int
    retrieval_candidates: int
    embedding_batch_size: int
    score_threshold: float
    rrf_k: int
    default_top_k: int
    rerank_enabled: bool
    rerank_top_n: int
    rerank_min_score: float
    rerank_timeout_cap: int
    trash_retention_days: int
    qa_record_retention_days: int
    login_rate_limit: int
    login_rate_window: int
    register_rate_limit: int
    register_rate_window: int
    ask_rate_limit: int
    ask_rate_window: int
    cors_origins: tuple[str, ...]
    docx_max_uncompressed_bytes: int
    pdf_max_pages: int
    doc_max_chars: int
    trust_proxy_headers: bool
    index_cache_max_users: int
    bcrypt_rounds: int
    frontend_dist: Path
    registration_mode: str
    registration_invite_code: str
    max_normal_users: int
    block_private_upstreams: bool
    model_host_allowlist: tuple[str, ...]
    upload_rate_limit: int
    upload_rate_window: int
    eval_rate_limit: int
    eval_rate_window: int

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DATA_DIR", str(BACKEND_DIR / "data"))).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        database_path = Path(
            os.getenv("DATABASE_PATH", str(data_dir / "app.db"))
        ).resolve()
        database_path.parent.mkdir(parents=True, exist_ok=True)
        chroma_dir = Path(os.getenv("CHROMA_DIR", str(data_dir / "chroma"))).resolve()
        chroma_dir.mkdir(parents=True, exist_ok=True)
        encryption_key_path = Path(
            os.getenv("ENCRYPTION_KEY_PATH", str(data_dir / ".encryption_key"))
        ).resolve()
        origins = tuple(
            item.strip() for item in os.getenv("CORS_ORIGINS", "").split(",") if item.strip()
        )
        app_env = (os.getenv("APP_ENV") or "dev").strip().lower() or "dev"
        registration_mode = (
            (os.getenv("REGISTRATION_MODE") or "open").strip().lower().replace("_", "-")
        )
        if registration_mode not in ("open", "first-user", "invite", "closed"):
            registration_mode = "open"
        allowlist = tuple(
            item.strip().lower().rstrip(".")
            for item in os.getenv("MODEL_HOST_ALLOWLIST", "").split(",")
            if item.strip()
        )
        app_secret = _secret(data_dir)
        if app_env == "production" and len(app_secret) < 32:
            raise RuntimeError(
                "APP_SECRET must be at least 32 characters in production; "
                "generate one with: openssl rand -hex 32"
            )
        return cls(
            app_env=app_env,
            backend_dir=BACKEND_DIR,
            data_dir=data_dir,
            database_path=database_path,
            chroma_dir=chroma_dir,
            app_secret=app_secret,
            encryption_key_path=encryption_key_path,
            token_ttl_seconds=_int("TOKEN_TTL_SECONDS", 86400),
            upload_max_bytes=_int("UPLOAD_MAX_BYTES", 50 * 1024 * 1024),
            chunk_size=_int("CHUNK_SIZE", 500),
            chunk_overlap=_int("CHUNK_OVERLAP", 50),
            retrieval_candidates=_int("RETRIEVAL_CANDIDATES", 20),
            embedding_batch_size=_int("EMBEDDING_BATCH_SIZE", 32),
            score_threshold=_float("SCORE_THRESHOLD", 0.6),
            rrf_k=_int("RRF_K", 60),
            default_top_k=_int("TOP_K", 4),
            rerank_enabled=_bool("RERANK_ENABLED", True),
            rerank_top_n=_int("RERANK_TOP_N", 20),
            rerank_min_score=_float("RERANK_MIN_SCORE", 0.0),
            rerank_timeout_cap=_int("RERANK_TIMEOUT_CAP", 5),
            trash_retention_days=_int("TRASH_RETENTION_DAYS", 7),
            qa_record_retention_days=_int("QA_RECORD_RETENTION_DAYS", 30),
            login_rate_limit=_int("LOGIN_RATE_LIMIT", 5),
            login_rate_window=_int("LOGIN_RATE_WINDOW", 60),
            register_rate_limit=_int("REGISTER_RATE_LIMIT", 5),
            register_rate_window=_int("REGISTER_RATE_WINDOW", 60),
            ask_rate_limit=_int("ASK_RATE_LIMIT", 10),
            ask_rate_window=_int("ASK_RATE_WINDOW", 60),
            cors_origins=origins,
            docx_max_uncompressed_bytes=_int("DOCX_MAX_UNCOMPRESSED_BYTES", 200 * 1024 * 1024),
            pdf_max_pages=_int("PDF_MAX_PAGES", 1000),
            doc_max_chars=_int("DOC_MAX_CHARS", 5_000_000),
            trust_proxy_headers=_bool("TRUST_PROXY_HEADERS", False),
            index_cache_max_users=_int("INDEX_CACHE_MAX_USERS", 8),
            bcrypt_rounds=_int("BCRYPT_ROUNDS", 12),
            frontend_dist=Path(
                os.getenv("FRONTEND_DIST", str(BACKEND_DIR.parent / "frontend" / "dist"))
            ).resolve(),
            registration_mode=registration_mode,
            registration_invite_code=(os.getenv("REGISTRATION_INVITE_CODE") or "").strip(),
            max_normal_users=max(0, _int("MAX_NORMAL_USERS", 10)),
            block_private_upstreams=_bool("BLOCK_PRIVATE_UPSTREAM", app_env == "production"),
            model_host_allowlist=allowlist,
            upload_rate_limit=_int("UPLOAD_RATE_LIMIT", 10),
            upload_rate_window=_int("UPLOAD_RATE_WINDOW", 60),
            eval_rate_limit=_int("EVAL_RATE_LIMIT", 3),
            eval_rate_window=_int("EVAL_RATE_WINDOW", 60),
        )


def get_settings() -> Settings:
    """Convenience for scripts; app code should use app.state.settings."""
    return Settings.from_env()