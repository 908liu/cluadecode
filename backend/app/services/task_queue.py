"""Persistent single-worker FIFO task queue."""
from __future__ import annotations

import queue
import threading

from sqlalchemy import select

from ..core.config import Settings
from ..core.utils import dump_json, load_json, new_id, utcnow_iso
from ..db.models import Document, EvalRun, Task
from ..db.session import Database
from . import eval_service, pipeline
from .index_cache import IndexCacheRegistry


class TaskQueue:
    def __init__(self, db: Database, settings: Settings, registry: IndexCacheRegistry) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopped = threading.Event()

    def submit(self, task_id: str) -> None:
        self._queue.put(task_id)

    def enqueue(self, session, user_id: str, task_type: str, payload: dict, total: int = 1) -> Task:
        task = Task(
            id=new_id("task"), user_id=user_id, type=task_type, status="queued",
            progress_done=0, progress_total=total, payload_json=dump_json(payload),
            attempts=0, created_at=utcnow_iso(),
        )
        session.add(task)
        session.commit()
        self._queue.put(task.id)
        return task

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stopped.clear()
        self._thread = threading.Thread(target=self._loop, name="kb-task-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopped.set()
        self._queue.put(None)

    def _loop(self) -> None:
        while not self._stopped.is_set():
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                break
            try:
                self._work(item)
            finally:
                self._queue.task_done()

    def _work(self, task_id: str) -> None:
        session = self.db.session()
        try:
            task = session.get(Task, task_id)
            if task is None or task.status != "queued":
                return
            task.status = "running"
            task.started_at = task.started_at or utcnow_iso()
            task.attempts += 1
            session.commit()
            try:
                self._dispatch(session, task)
            except Exception as exc:
                session.rollback()
                task = session.get(Task, task_id)
                if task is not None:
                    task.status = "failed"
                    task.error_code = "internal"
                    task.error_message = str(exc)
            finally:
                task = session.get(Task, task_id)
                if task is not None:
                    if task.status == "running":
                        task.status = "succeeded"
                        task.progress_done = task.progress_total
                    task.finished_at = task.finished_at or utcnow_iso()
                    session.commit()
        finally:
            session.close()

    def _dispatch(self, session, task: Task) -> None:
        payload = load_json(task.payload_json, {})
        if task.type == "document_parse":
            pipeline.process_document(
                session, self.settings, self.registry, task.user_id,
                payload["document_id"], payload["version_id"],
            )
            doc = session.get(Document, payload["document_id"])
            if doc is not None and doc.status == "failed":
                task.status = "failed"
                task.error_code = doc.error_code
                task.error_message = doc.error
            else:
                task.status = "succeeded"
                task.progress_done = task.progress_total
            session.commit()
        elif task.type == "document_reindex":
            pipeline.reindex_document(
                session, self.settings, self.registry, task.user_id, payload["document_id"]
            )
            doc = session.get(Document, payload["document_id"])
            if doc is not None and doc.status == "failed":
                task.status = "failed"
                task.error_code = doc.error_code
                task.error_message = doc.error
            else:
                task.status = "succeeded"
                task.progress_done = task.progress_total
            session.commit()
        elif task.type == "reindex_all":
            pipeline.reindex_all(session, self.settings, self.registry, task)
        elif task.type == "eval_run":
            eval_service.run_eval(session, self.settings, self.registry, task)
        elif task.type == "eval_rescore":
            eval_service.run_rescore(session, self.settings, self.registry, task)
        else:
            task.status = "failed"
            task.error_code = "unknown_task"
            task.error_message = f"未知任务类型：{task.type}"
            session.commit()

    def recover(self) -> None:
        session = self.db.session()
        try:
            tasks = session.scalars(
                select(Task).where(Task.status.in_(["queued", "running"])).order_by(Task.created_at)
            ).all()
            for task in tasks:
                if task.status == "running":
                    task.attempts += 1
                    if task.attempts >= 3:
                        task.status = "failed"
                        task.error_code = "startup_interrupted"
                        task.error_message = "任务多次因服务重启中断"
                        task.finished_at = utcnow_iso()
                        session.commit()
                        continue
                    task.status = "queued"
                    session.commit()
                self._queue.put(task.id)
            for doc in session.scalars(
                select(Document).where(
                    Document.status.in_(["queued", "parsing", "chunking", "indexing"]),
                    Document.deleted_at.is_(None),
                )
            ).all():
                live = session.scalar(
                    select(Task).where(
                        Task.status.in_(["queued", "running"]),
                        Task.payload_json.like(f'%"{doc.id}"%'),
                    )
                )
                if live is None:
                    doc.status = "failed"
                    doc.error_code = "internal"
                    doc.error = "服务重启导致处理中断，请重试"
                    doc.updated_at = utcnow_iso()
            session.commit()
        finally:
            session.close()