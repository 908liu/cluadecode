"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .api.deps import RateLimiter
from .api.routers import auth, documents, eval_routes, qa, search, settings as settings_router, system
from .core.config import Settings
from .core.errors import ApiError, api_error_handler, unhandled_exception_handler, validation_exception_handler
from .core.security import ensure_encryption_key
from .core.utils import new_id, utcnow_iso
from .db.models import Chunk, Document, DocumentVersion, QaRecord, ReindexTaskItem, Task
from .db.session import Database
from .services import eval_service, files, keyword_index
from .services.index_cache import IndexCacheRegistry
from .services.task_queue import TaskQueue
from .services.vector_store import VectorStore


def _cleanup(db: Session, settings: Settings, registry: IndexCacheRegistry) -> None:
    now = utcnow_iso()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=settings.qa_record_retention_days)).replace(
        microsecond=0
    ).isoformat()
    db.execute(delete(QaRecord).where(QaRecord.created_at < cutoff))
    expired = db.scalars(
        select(Document).where(
            Document.deleted_at.is_not(None), Document.purge_at.is_not(None), Document.purge_at < now
        )
    ).all()
    for doc in expired:
        document_id = doc.id
        registry.delete_document_everywhere(db, document_id)
        db.execute(delete(Chunk).where(Chunk.document_id == document_id))
        db.execute(delete(DocumentVersion).where(DocumentVersion.document_id == document_id))
        db.execute(delete(ReindexTaskItem).where(ReindexTaskItem.document_id == document_id))
        db.execute(delete(Task).where(Task.payload_json.like(f'%"{document_id}"%')))
        db.delete(doc)
        db.commit()
        files.delete_document_files(settings, document_id)
    db.commit()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    database = Database(settings)
    vector_store = VectorStore(settings)
    registry = IndexCacheRegistry(settings, vector_store)
    task_queue = TaskQueue(database, settings, registry)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        ensure_encryption_key(settings)
        database.create_all()
        keyword_index.ensure_schema(database.engine)
        session = database.session()
        try:
            eval_service.seed_defaults(session, settings)
            eval_service.seed_eval_dataset(session)
            _cleanup(session, settings, registry)
            registry.cleanup_startup(session)
        finally:
            session.close()
        task_queue.start()
        task_queue.recover()
        yield
        task_queue.stop()
        database.dispose()

    production = settings.app_env == "production"
    app = FastAPI(
        title="医知助手 - 医疗指南与流程知识库",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.db = database
    app.state.registry = registry
    app.state.vector_store = vector_store
    app.state.tasks = task_queue
    app.state.login_limiter = RateLimiter()
    app.state.register_limiter = RateLimiter()
    app.state.ask_limiter = RateLimiter()
    app.state.upload_limiter = RateLimiter()
    app.state.eval_limiter = RateLimiter()

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(settings.cors_origins),
            allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_id = new_id("req")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if production:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data: blob:; "
                "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "connect-src 'self'; font-src 'self' data:; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'none'",
            )
        return response

    api = "/api/v1"
    app.include_router(auth.router, prefix=api)
    app.include_router(system.router, prefix=api)
    app.include_router(settings_router.router, prefix=api)
    app.include_router(documents.router, prefix=api)
    app.include_router(qa.router, prefix=api)
    app.include_router(eval_routes.router, prefix=api)
    app.include_router(search.router, prefix=api)

    @app.get("/health")
    def health_root():
        session = database.session()
        try:
            return system.health_payload(session)
        finally:
            session.close()

    if settings.frontend_dist.exists():
        assets = settings.frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        if full_path.startswith("api/") or full_path == "api":
            return JSONResponse(status_code=404, content={"code": 40401, "message": "接口不存在", "detail": None})
        index_file = settings.frontend_dist / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return JSONResponse({
            "name": "医知助手 API",
            "docs": "/docs",
            "health": "/health",
            "frontend": "frontend/dist 不存在，开发期请使用 Vite dev server（5173）",
        })

    return app


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:create_app", factory=True, host="127.0.0.1", port=8000, workers=1)