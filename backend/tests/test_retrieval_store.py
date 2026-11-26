from __future__ import annotations

import dataclasses
import sqlite3
import uuid
from types import SimpleNamespace

import chromadb
from sqlalchemy import create_engine, inspect

from app.core.config import Settings
from app.db.session import Database
from app.services import keyword_index
from app.services.vector_store import VectorStore


def _chunk(chunk_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(id=chunk_id, text=text)


def test_tokenize_cjk_and_ascii() -> None:
    tokens = keyword_index.tokenize("PDF 回收站 v4")
    assert "pdf" in tokens
    assert {"回", "收", "站", "回收", "收站"} <= set(tokens)
    assert "v4" in tokens
    assert tokens.count("回") == 1


def test_fts5_search_is_scoped_to_index_version() -> None:
    engine = create_engine("sqlite://")
    keyword_index.ensure_schema(engine)
    with engine.begin() as connection:
        keyword_index.replace_document(
            connection, "idx_a", "d_a", [_chunk("c1", "回收站保留 7 天")]
        )
        keyword_index.replace_document(
            connection, "idx_b", "d_b", [_chunk("c2", "文档上传格式说明")]
        )
    with engine.connect() as connection:
        hits = keyword_index.search(connection, "idx_a", "回收站", 20)
        assert [hit["chunk_id"] for hit in hits] == ["c1"]
        assert hits[0]["score"] > 0
        assert keyword_index.search(connection, "idx_b", "回收站", 20) == []
    with engine.begin() as connection:
        keyword_index.delete_document(connection, "idx_a", "d_a")
    with engine.connect() as connection:
        assert keyword_index.search(connection, "idx_a", "回收站", 20) == []


def test_vector_store_upsert_query_and_delete() -> None:
    settings = Settings.from_env()
    store = VectorStore(settings, client=chromadb.EphemeralClient())
    index_id = f"idx_{uuid.uuid4().hex[:16]}"
    chunks = [_chunk("c1", "回收站"), _chunk("c2", "上传格式")]
    store.upsert(index_id, "d1", chunks, [[1.0, 0.0], [0.0, 1.0]])

    near_first = store.query(index_id, [0.9, 0.1], 2)
    assert near_first and near_first[0][0] == "c1"
    assert near_first[0][1] > 0.9

    store.delete_document(index_id, "d1")
    assert store.query(index_id, [1.0, 0.0], 2) == []

    store.upsert(index_id, "d2", [_chunk("c3", "新文档")], [[1.0, 0.0]])
    store.delete_index(index_id)
    assert store.query(index_id, [1.0, 0.0], 2) is None


def test_old_index_members_schema_is_migrated(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database_path = data_dir / "app.db"
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE index_members ("
        "index_version_id TEXT NOT NULL, chunk_id TEXT NOT NULL, "
        "document_id TEXT NOT NULL, vector BLOB NOT NULL, norm REAL NOT NULL, "
        "PRIMARY KEY (index_version_id, chunk_id))"
    )
    connection.commit()
    connection.close()

    base = Settings.from_env()
    settings = dataclasses.replace(
        base,
        data_dir=data_dir,
        database_path=database_path,
        chroma_dir=data_dir / "chroma",
        encryption_key_path=data_dir / ".encryption_key",
    )
    database = Database(settings)
    try:
        database.create_all()
        columns = {column["name"] for column in inspect(database.engine).get_columns("index_members")}
        assert columns == {"index_version_id", "chunk_id", "document_id"}
    finally:
        database.dispose()