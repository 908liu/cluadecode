"""SQLite FTS5 keyword index with a lightweight CJK tokenizer."""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy.engine import Connection, Engine

_TABLE = "chunk_fts"
_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)
_CLEAN = re.compile(r'["*]')


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """ASCII words plus CJK unigrams and bigrams, deduplicated in order."""
    normalized = unicodedata.normalize("NFKC", text or "").casefold()
    tokens: list[str] = []
    ascii_buffer: list[str] = []
    cjk_buffer: list[str] = []

    def flush_ascii() -> None:
        if ascii_buffer:
            tokens.append("".join(ascii_buffer))
            ascii_buffer.clear()

    def flush_cjk() -> None:
        if not cjk_buffer:
            return
        run = "".join(cjk_buffer)
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        cjk_buffer.clear()

    for char in normalized:
        if _is_cjk(char):
            flush_ascii()
            cjk_buffer.append(char)
        elif char.isascii() and char.isalnum():
            flush_cjk()
            ascii_buffer.append(char)
        else:
            flush_ascii()
            flush_cjk()
    flush_ascii()
    flush_cjk()

    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = _CLEAN.sub("", token)
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def match_expression(tokens: list[str], limit: int = 64) -> str:
    clauses: list[str] = []
    for token in tokens[:limit]:
        safe = _CLEAN.sub("", token)
        if safe:
            clauses.append(f'"{safe}"')
    return " OR ".join(clauses)


def ensure_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE} USING fts5("
            "chunk_id UNINDEXED, index_version_id UNINDEXED, "
            "document_id UNINDEXED, tokens, tokenize='unicode61')"
        )


def replace_document(
    connection: Connection,
    index_version_id: str,
    document_id: str,
    chunks: list,
) -> None:
    delete_document(connection, index_version_id, document_id)
    rows = [
        (
            chunk.id,
            index_version_id,
            document_id,
            " ".join(tokenize(chunk.text)),
        )
        for chunk in chunks
    ]
    if rows:
        connection.exec_driver_sql(
            f"INSERT INTO {_TABLE}(chunk_id, index_version_id, document_id, tokens) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )


def delete_document(connection: Connection, index_version_id: str, document_id: str) -> None:
    connection.exec_driver_sql(
        f"DELETE FROM {_TABLE} WHERE index_version_id = ? AND document_id = ?",
        (index_version_id, document_id),
    )


def delete_index(connection: Connection, index_version_id: str) -> None:
    connection.exec_driver_sql(
        f"DELETE FROM {_TABLE} WHERE index_version_id = ?", (index_version_id,)
    )


def delete_orphans(connection: Connection) -> None:
    connection.exec_driver_sql(
        f"DELETE FROM {_TABLE} "
        "WHERE index_version_id NOT IN (SELECT id FROM index_versions)"
    )


def search(
    connection: Connection, index_version_id: str, query: str, limit: int
) -> list[dict]:
    expression = match_expression(tokenize(query))
    if not expression:
        return []
    rows = connection.exec_driver_sql(
        f"SELECT chunk_id, -bm25({_TABLE}) AS score FROM {_TABLE} "
        f"WHERE {_TABLE} MATCH ? AND index_version_id = ? "
        f"ORDER BY bm25({_TABLE}) ASC LIMIT ?",
        (expression, index_version_id, limit),
    ).fetchall()
    return [
        {"chunk_id": row[0], "score": float(row[1])}
        for row in rows
        if row[1] is not None and float(row[1]) > 0
    ]