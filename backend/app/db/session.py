"""SQLite engine, sessions and schema bootstrap."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..core.config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_engine(
            f"sqlite:///{settings.database_path}",
            connect_args={"check_same_thread": False},
            future=True,
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session
        )

        @event.listens_for(self.engine, "connect")
        def _pragmas(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    def create_all(self) -> None:
        from . import models  # noqa: F401

        self._migrate_index_members()
        Base.metadata.create_all(bind=self.engine)
        self._migrate_rag_v2()

    def _migrate_rag_v2(self) -> None:
        """Add v2 rerank/NDCG columns without rebuilding existing tables."""
        additions = (
            ("api_services", "protocol", "VARCHAR(16) NOT NULL DEFAULT 'cohere'"),
            ("eval_run_cases", "ndcg_score", "REAL"),
        )
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        with self.engine.begin() as connection:
            for table, column, ddl in additions:
                if table not in tables:
                    continue
                columns = {item["name"] for item in inspector.get_columns(table)}
                if column not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _migrate_index_members(self) -> None:
        """Drop the v1.1 vector column; v1.2 keeps vectors in Chroma."""
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        if "index_members" not in tables:
            return
        columns = {column["name"] for column in inspector.get_columns("index_members")}
        if "vector" not in columns:
            return
        with self.engine.begin() as connection:
            connection.exec_driver_sql("DROP TABLE index_members")
            if "index_versions" in tables:
                connection.exec_driver_sql(
                    "UPDATE index_versions SET is_active=0, status='retired'"
                )
            if "documents" in tables:
                connection.exec_driver_sql(
                    "UPDATE documents SET reindex_required=1 WHERE status='available'"
                )

    def session(self) -> Session:
        return self.session_factory()

    def dispose(self) -> None:
        self.engine.dispose()


def session_scope(db: Database) -> Iterator[Session]:
    session = db.session()
    try:
        yield session
    finally:
        session.close()