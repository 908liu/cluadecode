"""Local Chroma persistence and per-index-version vector collections."""
from __future__ import annotations

import threading

import chromadb
from chromadb.config import Settings as ChromaSettings

from ..core.config import Settings

_BATCH_SIZE = 500


class VectorStore:
    """One Chroma collection per IndexVersion; cosine space."""

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        if client is None:
            settings.chroma_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(
                path=str(settings.chroma_dir),
                settings=ChromaSettings(anonymized_telemetry=False),
            )
        self._client = client

    @staticmethod
    def collection_name(index_version_id: str) -> str:
        return f"idx_{index_version_id}"

    def get_collection(self, index_version_id: str):
        try:
            return self._client.get_collection(self.collection_name(index_version_id))
        except Exception:
            return None

    def ensure_collection(self, index_version_id: str):
        name = self.collection_name(index_version_id)
        with self._lock:
            return self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"}
            )

    def upsert(self, index_version_id: str, document_id: str, chunks: list, vectors: list[list[float]]) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError("chunk 与向量数量不一致")
        collection = self.ensure_collection(index_version_id)
        with self._lock:
            for start in range(0, len(chunks), _BATCH_SIZE):
                batch_chunks = chunks[start : start + _BATCH_SIZE]
                batch_vectors = vectors[start : start + _BATCH_SIZE]
                collection.upsert(
                    ids=[chunk.id for chunk in batch_chunks],
                    embeddings=batch_vectors,
                    metadatas=[{"document_id": document_id} for _ in batch_chunks],
                )

    def delete_document(self, index_version_id: str, document_id: str) -> None:
        collection = self.get_collection(index_version_id)
        if collection is None:
            return
        with self._lock:
            existing = collection.get(where={"document_id": document_id}, include=[])
            ids = existing.get("ids") or []
            if ids:
                collection.delete(ids=ids)

    def delete_index(self, index_version_id: str) -> None:
        name = self.collection_name(index_version_id)
        with self._lock:
            try:
                self._client.delete_collection(name)
            except Exception:
                return

    def query(self, index_version_id: str, vector: list[float], limit: int) -> list[tuple[str, float]] | None:
        collection = self.get_collection(index_version_id)
        if collection is None:
            return None
        count = collection.count()
        if count == 0:
            return []
        result = collection.query(
            query_embeddings=[vector],
            n_results=min(limit, count),
            include=["distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return [
            (chunk_id, 1.0 - float(distance))
            for chunk_id, distance in zip(ids, distances)
        ]

    def delete_orphan_collections(self, valid_index_ids: set[str]) -> None:
        valid_names = {self.collection_name(index_id) for index_id in valid_index_ids}
        with self._lock:
            for item in self._client.list_collections():
                name = getattr(item, "name", str(item))
                if name.startswith("idx_") and name not in valid_names:
                    try:
                        self._client.delete_collection(name)
                    except Exception:
                        continue