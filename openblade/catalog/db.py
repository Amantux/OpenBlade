"""Database setup for the SQLite-backed OpenBlade catalog."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from openblade.catalog.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_db_url: str | None = None


def _normalize_db_url(db_url: str) -> str:
    if db_url.startswith("sqlite+aiosqlite:///"):
        return db_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    return db_url


def _sqlite_connect_args(db_url: str) -> dict[str, object]:
    if db_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def _ensure_parent_dir(db_url: str) -> None:
    for prefix in ("sqlite:///", "sqlite+pysqlite:///"):
        if db_url.startswith(prefix) and ":memory:" not in db_url:
            db_path = Path(db_url.removeprefix(prefix)).expanduser()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return


def _migrate_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    if "file_records" not in inspector.get_table_names():
        return
    existing_columns = {column["name"] for column in inspector.get_columns("file_records")}
    required_columns = {
        "shard_count": "INTEGER",
        "shard_index": "INTEGER",
        "block_size": "INTEGER",
        "shard_profile": "VARCHAR(32)",
        "parent_id": "VARCHAR",
    }
    missing_columns = {
        name: column_type
        for name, column_type in required_columns.items()
        if name not in existing_columns
    }
    if not missing_columns:
        return
    with engine.begin() as connection:
        for name, column_type in missing_columns.items():
            connection.execute(text(f"ALTER TABLE file_records ADD COLUMN {name} {column_type}"))
        if "parent_id" in missing_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_file_records_parent_id ON file_records (parent_id)"))


def init_db(db_url: str = "sqlite:///./openblade.db") -> None:
    global _engine, _SessionLocal, _db_url
    normalized_db_url = _normalize_db_url(db_url)
    if (
        _engine is not None
        and _db_url == normalized_db_url
        and not normalized_db_url.endswith(":memory:")
    ):
        _migrate_schema(_engine)
        return
    if _engine is not None:
        _engine.dispose()
    _ensure_parent_dir(normalized_db_url)
    engine_kwargs: dict[str, object] = {
        "echo": False,
        "future": True,
        "connect_args": _sqlite_connect_args(normalized_db_url),
    }
    if normalized_db_url.endswith(":memory:"):
        engine_kwargs["poolclass"] = StaticPool
    _engine = create_engine(normalized_db_url, **engine_kwargs)
    Base.metadata.create_all(_engine)
    _migrate_schema(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, class_=Session)
    _db_url = normalized_db_url


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()
