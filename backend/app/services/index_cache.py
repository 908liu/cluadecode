"""Active index snapshots, Chroma/FTS5 retrieval and RRF fusion."""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..core.errors import ApiError, service_missing
from ..db.models import Chunk, Document, DocumentVersion, EvalRun, IndexMember, IndexVersion
from . import keyword_index, upstream


class IndexWriteError(Exception):
    """Raised when Chroma/FTS5/membership writes cannot be completed."""

    code = "index_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class IndexSnapshot:
    version_id: str | None
    embedding_service_id: str | None
    model: str | None
    dimensions: int | None

    @property
    def has_index(self) -> bool:
        return self.version_id is not None


class IndexCacheRegistry:
    def __init__(self, settings: Settings, vector_store=None) -> None:
        from .vector_store import VectorStore

        self.settings = settings
        self.vector_store = vector_store or VectorStore(settings)
        self._lock = threading.RLock()
        self._snapshots: OrderedDict[str, IndexSnapshot] = OrderedDict()

    def _get(self, user_id: str) -> IndexSnapshot | None:
        with self._lock:
            snapshot = self._snapshots.get(user_id)
            if snapshot is not None:
                self._snapshots.move_to_end(user_id)
            return snapshot

    def _put(self, user_id: str, snapshot: IndexSnapshot) -> None:
        with self._lock:
            self._snapshots[user_id] = snapshot
            self._snapshots.move_to_end(user_id)
            while len(self._snapshots) > self.settings.index_cache_max_users:
                self._snapshots.popitem(last=False)

    def invalidate(self, user_id: str) -> None:
        with self._lock:
            self._snapshots.pop(user_id, None)

    def load(self, db: Session, user_id: str) -> IndexSnapshot:
        active = db.scalar(
            select(IndexVersion).where(
                IndexVersion.user_id == user_id, IndexVersion.is_active.is_(True)
            )
        )
        snapshot = (
            IndexSnapshot(
                active.id,
                active.embedding_service_id,
                active.model,
                active.dimensions,
            )
            if active
            else IndexSnapshot(None, None, None, None)
        )
        self._put(user_id, snapshot)
        return snapshot

    def get(self, db: Session, user_id: str) -> IndexSnapshot:
        snapshot = self._get(user_id)
        return snapshot if snapshot is not None else self.load(db, user_id)

    def rebuild(self, db: Session, user_id: str) -> IndexSnapshot:
        return self.load(db, user_id)

    def _snapshot_service(
        self, db: Session, user_id: str, snapshot: IndexSnapshot
    ) -> upstream.ServiceInfo | None:
        if snapshot.embedding_service_id:
            service = upstream.service_by_id(
                db, self.settings, user_id, snapshot.embedding_service_id
            )
        else:
            service = upstream.env_service(self.settings, "embedding")
        if service is None or not service.enabled:
            return None
        if snapshot.model and service.model != snapshot.model:
            return None
        if (
            snapshot.dimensions is not None
            and service.dimensions is not None
            and service.dimensions != snapshot.dimensions
        ):
            return None
        return service

    def replace_document(
        self,
        db: Session,
        index: IndexVersion,
        document_id: str,
        chunks: list,
        vectors: list[list[float]],
    ) -> None:
        collection = self.vector_store.get_collection(index.id)
        if collection is None:
            existing = int(
                db.scalar(
                    select(func.count())
                    .select_from(IndexMember)
                    .where(IndexMember.index_version_id == index.id)
                )
                or 0
            )
            if index.is_active and existing:
                raise IndexWriteError("索引数据缺失，请重建索引")
            self.vector_store.ensure_collection(index.id)
        try:
            self.vector_store.delete_document(index.id, document_id)
            self.vector_store.upsert(index.id, document_id, chunks, vectors)
            keyword_index.replace_document(
                db.connection(), index.id, document_id, chunks
            )
            db.execute(
                delete(IndexMember).where(
                    IndexMember.index_version_id == index.id,
                    IndexMember.document_id == document_id,
                )
            )
            db.add_all(
                [
                    IndexMember(
                        index_version_id=index.id,
                        chunk_id=chunk.id,
                        document_id=document_id,
                    )
                    for chunk in chunks
                ]
            )
        except Exception as exc:
            try:
                self.vector_store.delete_document(index.id, document_id)
            except Exception:
                pass
            try:
                keyword_index.delete_document(db.connection(), index.id, document_id)
            except Exception:
                pass
            raise IndexWriteError(f"索引写入失败：{exc}") from exc

    def delete_document(self, db: Session, index_version_id: str, document_id: str) -> None:
        try:
            self.vector_store.delete_document(index_version_id, document_id)
        except Exception:
            pass
        try:
            keyword_index.delete_document(db.connection(), index_version_id, document_id)
        except Exception:
            pass
        db.execute(
            delete(IndexMember).where(
                IndexMember.index_version_id == index_version_id,
                IndexMember.document_id == document_id,
            )
        )

    def delete_document_everywhere(self, db: Session, document_id: str) -> None:
        index_ids = set(
            db.scalars(
                select(IndexMember.index_version_id).where(
                    IndexMember.document_id == document_id
                )
            ).all()
        )
        for index_version_id in index_ids:
            self.delete_document(db, index_version_id, document_id)
        db.execute(delete(IndexMember).where(IndexMember.document_id == document_id))

    def drop_index(self, db: Session, index_version_id: str) -> None:
        try:
            self.vector_store.delete_index(index_version_id)
        except Exception:
            pass
        try:
            keyword_index.delete_index(db.connection(), index_version_id)
        except Exception:
            pass
        db.execute(
            delete(IndexMember).where(
                IndexMember.index_version_id == index_version_id
            )
        )

    def cleanup_retired(self, db: Session, user_id: str | None = None) -> None:
        protected = set(
            db.scalars(
                select(EvalRun.index_version_id).where(
                    EvalRun.status.in_(("queued", "running")),
                    EvalRun.index_version_id.is_not(None),
                )
            ).all()
        )
        query = select(IndexVersion).where(
            IndexVersion.is_active.is_(False), IndexVersion.status == "retired"
        )
        if user_id is not None:
            query = query.where(IndexVersion.user_id == user_id)
        for row in db.scalars(query).all():
            if row.id in protected:
                continue
            self.drop_index(db, row.id)
        db.commit()

    def cleanup_startup(self, db: Session) -> None:
        try:
            keyword_index.delete_orphans(db.connection())
        except Exception:
            pass
        valid_ids = set(db.scalars(select(IndexVersion.id)).all())
        try:
            self.vector_store.delete_orphan_collections(valid_ids)
        except Exception:
            pass
        self.cleanup_retired(db)

    def _chunk_map(self, db: Session, user_id: str, chunk_ids: list[str]) -> dict[str, dict]:
        if not chunk_ids:
            return {}
        rows = db.execute(
            select(Chunk, DocumentVersion, Document)
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Chunk.id.in_(chunk_ids),
                Document.user_id == user_id,
                Document.deleted_at.is_(None),
                Document.status == "available",
            )
        ).all()
        result: dict[str, dict] = {}
        for chunk, version, document in rows:
            result[chunk.id] = {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "document_title": document.title,
                "version": version.version,
                "page": chunk.page,
                "paragraph": chunk.paragraph,
                "text": chunk.text,
                "uploader": document.uploader,
                "snippet": chunk.text[:200],
            }
        return result

    def retrieve(
        self,
        db: Session,
        settings: Settings,
        user_id: str,
        query: str,
        top_k: int,
        threshold: float,
        rrf_k: int | None = None,
        debug: bool = False,
        snapshot: IndexSnapshot | None = None,
        use_rerank: bool = False,
        rerank_top_n: int | None = None,
        rerank_service: upstream.ServiceInfo | None = None,
        retrieval_strategy: str = "hybrid",
    ) -> dict:
        snapshot = snapshot or self.get(db, user_id)
        rrf_k = rrf_k or settings.rrf_k
        rerank_meta = {
            "enabled": False, "applied": False, "candidates": 0,
            "top_n": int(rerank_top_n or settings.rerank_top_n),
            "error": None, "service_id": None, "model": None,
        }
        empty = {
            "keyword": [],
            "vector": [],
            "fused": [],
            "reranked": [],
            "final": [],
            "chunks": [],
            "rerank": rerank_meta,
            "version_id": snapshot.version_id,
        }
        if not snapshot.has_index:
            return empty
        service = self._snapshot_service(db, user_id, snapshot)
        if service is None:
            raise service_missing("embedding")

        strategy = retrieval_strategy if retrieval_strategy in ("dense", "hybrid", "hybrid_rerank") else "hybrid"
        if strategy == "dense":
            keyword_rows = []
        else:
            try:
                keyword_rows = keyword_index.search(
                    db.connection(),
                    snapshot.version_id,
                    query,
                    settings.retrieval_candidates,
                )
            except Exception as exc:
                raise ApiError(
                    503,
                    50302,
                    "索引数据缺失，请重建索引",
                    {"type": "embedding", "index_version_id": snapshot.version_id},
                ) from exc

        try:
            query_vector = upstream.embed_texts(service, [query])[0]
        except upstream.UpstreamError as exc:
            if exc.error_type == "timeout":
                raise ApiError(
                    504,
                    50401,
                    "模型服务响应超时",
                    {
                        "service_type": "embedding",
                        "timeout_seconds": service.timeout_seconds,
                    },
                ) from exc
            if exc.error_type == "rate_limit":
                raise ApiError(
                    429,
                    42901,
                    "模型服务限流，请稍后重试",
                    {"retry_after_seconds": 3},
                ) from exc
            raise ApiError(
                502,
                50201,
                "模型服务调用失败",
                {"service_type": "embedding", "upstream_status": exc.status_code},
            ) from exc

        try:
            vector_rows = self.vector_store.query(
                snapshot.version_id, query_vector, settings.retrieval_candidates
            )
        except Exception as exc:
            raise ApiError(
                503,
                50302,
                "索引数据缺失，请重建索引",
                {"type": "embedding", "index_version_id": snapshot.version_id},
            ) from exc
        if vector_rows is None:
            raise ApiError(
                503,
                50302,
                "索引数据缺失，请重建索引",
                {"type": "embedding", "index_version_id": snapshot.version_id},
            )
        vector_rows = [
            (chunk_id, similarity)
            for chunk_id, similarity in vector_rows
            if similarity >= threshold
        ]

        fused: dict[str, float] = {}
        for rank, row in enumerate(keyword_rows, start=1):
            fused[row["chunk_id"]] = fused.get(row["chunk_id"], 0.0) + 1.0 / (
                rrf_k + rank
            )
        for rank, (chunk_id, _score) in enumerate(vector_rows, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
        ranked_all = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
        ranked = ranked_all[:top_k]

        candidate_ids = list(
            dict.fromkeys(
                [row["chunk_id"] for row in keyword_rows]
                + [chunk_id for chunk_id, _score in vector_rows]
                + [chunk_id for chunk_id, _score in ranked_all]
            )
        )
        chunk_map = self._chunk_map(db, user_id, candidate_ids)
        ranked_valid = [
            (chunk_id, score) for chunk_id, score in ranked_all if chunk_id in chunk_map
        ]

        rrf_scores = dict(ranked_all)
        rerank_scores: dict[str, float] = {}
        reranked_debug: list[dict] = []
        final_order = [chunk_id for chunk_id, _score in ranked_valid[:top_k]]

        if (strategy == "hybrid_rerank" or use_rerank) and settings.rerank_enabled:
            rerank_meta["enabled"] = True
            rerank_meta["top_n"] = max(1, min(int(rerank_top_n or settings.rerank_top_n), 100))
            effective_rerank = rerank_service or upstream.effective_service(
                db, settings, user_id, "rerank"
            )
            if effective_rerank is None or not effective_rerank.enabled:
                rerank_meta["error"] = "not_configured"
            else:
                rerank_meta["service_id"] = effective_rerank.id
                rerank_meta["model"] = effective_rerank.model
                candidates = ranked_valid[: rerank_meta["top_n"]]
                rerank_meta["candidates"] = len(candidates)
                if candidates:
                    documents = [
                        f"{chunk_map[chunk_id]['document_title']}\n{chunk_map[chunk_id]['text']}"[:4000]
                        for chunk_id, _score in candidates
                    ]
                    try:
                        results = upstream.rerank_texts(
                            effective_rerank,
                            query,
                            documents,
                            len(documents),
                            timeout_cap=settings.rerank_timeout_cap,
                        )
                        seen: set[str] = set()
                        ordered_ids: list[str] = []
                        for index, score in results:
                            if index < 0 or index >= len(candidates):
                                continue
                            if settings.rerank_min_score > 0 and score < settings.rerank_min_score:
                                continue
                            chunk_id = candidates[index][0]
                            if chunk_id in seen:
                                continue
                            seen.add(chunk_id)
                            rerank_scores[chunk_id] = score
                            ordered_ids.append(chunk_id)
                            reranked_debug.append(
                                {
                                    "chunk_id": chunk_id,
                                    "document_id": chunk_map[chunk_id]["document_id"],
                                    "rrf_score": round(rrf_scores.get(chunk_id, 0.0), 8),
                                    "rerank_score": round(score, 6),
                                    "snippet": chunk_map[chunk_id]["snippet"],
                                }
                            )
                        if settings.rerank_min_score > 0:
                            final_order = ordered_ids[:top_k]
                        else:
                            remainder = [
                                chunk_id
                                for chunk_id, _score in ranked_valid
                                if chunk_id not in seen
                            ]
                            final_order = (ordered_ids + remainder)[:top_k]
                        rerank_meta["applied"] = True
                    except upstream.UpstreamError as exc:
                        rerank_meta["error"] = exc.error_type
                    except Exception:
                        rerank_meta["error"] = "internal"

        final_chunks = []
        for chunk_id in final_order:
            item = chunk_map.get(chunk_id)
            if item is None:
                continue
            chunk = {**item, "rrf_score": rrf_scores.get(chunk_id, 0.0)}
            if chunk_id in rerank_scores:
                chunk["rerank_score"] = rerank_scores[chunk_id]
            final_chunks.append(chunk)

        return {
            "keyword": [
                {
                    "chunk_id": row["chunk_id"],
                    "document_id": chunk_map[row["chunk_id"]]["document_id"],
                    "score": round(row["score"], 6),
                    "source": "bm25",
                    "snippet": chunk_map[row["chunk_id"]]["snippet"],
                }
                for row in keyword_rows
                if row["chunk_id"] in chunk_map
            ],
            "vector": [
                {
                    "chunk_id": chunk_id,
                    "document_id": chunk_map[chunk_id]["document_id"],
                    "score": round(score, 6),
                    "snippet": chunk_map[chunk_id]["snippet"],
                }
                for chunk_id, score in vector_rows
                if chunk_id in chunk_map
            ],
            "fused": [
                {
                    "chunk_id": chunk_id,
                    "rrf_score": round(score, 8),
                    "snippet": chunk_map[chunk_id]["snippet"],
                }
                for chunk_id, score in ranked
                if chunk_id in chunk_map
            ],
            "reranked": reranked_debug,
            "final": [
                {
                    "chunk_id": item["chunk_id"],
                    "document_id": item["document_id"],
                    "document_title": item["document_title"],
                    "page": item["page"],
                    "text": item["text"],
                    "rrf_score": round(item.get("rrf_score", 0.0), 8),
                    "rerank_score": (
                        round(item["rerank_score"], 6)
                        if item.get("rerank_score") is not None
                        else None
                    ),
                }
                for item in final_chunks
            ],
            "chunks": final_chunks,
            "rerank": rerank_meta,
            "version_id": snapshot.version_id,
        }