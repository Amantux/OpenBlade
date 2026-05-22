"""Database setup for the SQLite-backed OpenBlade catalog."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from openblade.catalog.models import Base
from openblade.catalog.repository import CatalogRepository

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
    existing_columns: set[str] = set()
    if "file_records" in inspector.get_table_names():
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
        if existing_columns and name not in existing_columns
    }
    restore_job_columns: set[str] = set()
    if "nas_restore_jobs" in inspector.get_table_names():
        restore_job_columns = {column["name"] for column in inspector.get_columns("nas_restore_jobs")}
    missing_restore_job_columns = (
        {"partial_success": "BOOLEAN NOT NULL DEFAULT 0"}
        if restore_job_columns and "partial_success" not in restore_job_columns
        else {}
    )
    cartridge_columns: set[str] = set()
    if "cartridges" in inspector.get_table_names():
        cartridge_columns = {column["name"] for column in inspector.get_columns("cartridges")}
    missing_cartridge_columns = (
        {"library_id": "INTEGER REFERENCES library_instances(id)"}
        if cartridge_columns and "library_id" not in cartridge_columns
        else {}
    )
    with engine.begin() as connection:
        if missing_columns:
            for name, column_type in missing_columns.items():
                connection.execute(text(f"ALTER TABLE file_records ADD COLUMN {name} {column_type}"))
        if existing_columns:
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_file_records_parent_id ON file_records (parent_id)")
            )
        if missing_restore_job_columns:
            for name, column_type in missing_restore_job_columns.items():
                connection.execute(text(f"ALTER TABLE nas_restore_jobs ADD COLUMN {name} {column_type}"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS library_instances (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR NOT NULL UNIQUE,
                    emulator_url VARCHAR NOT NULL,
                    serial_number VARCHAR,
                    model VARCHAR NOT NULL DEFAULT 'Scalar i3',
                    role TEXT DEFAULT 'primary',
                    sort_order INTEGER DEFAULT 0,
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        library_columns: set[str] = set()
        if "library_instances" in inspector.get_table_names():
            library_columns = {column["name"] for column in inspector.get_columns("library_instances")}
        if library_columns and "role" not in library_columns:
            with suppress(Exception):
                connection.execute(text("ALTER TABLE library_instances ADD COLUMN role TEXT DEFAULT 'primary'"))
        if library_columns and "sort_order" not in library_columns:
            with suppress(Exception):
                connection.execute(text("ALTER TABLE library_instances ADD COLUMN sort_order INTEGER DEFAULT 0"))
        if missing_cartridge_columns:
            for name, column_type in missing_cartridge_columns.items():
                connection.execute(text(f"ALTER TABLE cartridges ADD COLUMN {name} {column_type}"))
        if cartridge_columns:
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_cartridges_library_id ON cartridges (library_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_storage_policies (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    policy_type VARCHAR(64) NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_cache_drives (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    root_path VARCHAR NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_configs (
                    key VARCHAR PRIMARY KEY,
                    value_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_shares (
                    path VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    share_type VARCHAR(32) NOT NULL,
                    config_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_pools (
                    id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL UNIQUE,
                    description TEXT,
                    volume_group_ids TEXT NOT NULL DEFAULT '[]',
                    default_policy_id VARCHAR,
                    default_ingest_mode VARCHAR NOT NULL DEFAULT 'cache_drive',
                    mount_path TEXT,
                    virtual_mount_enabled INTEGER NOT NULL DEFAULT 1,
                    hydration_behavior VARCHAR NOT NULL DEFAULT 'queue',
                    cache_target_id VARCHAR,
                    restore_target_path TEXT NOT NULL DEFAULT '/openblade/restore',
                    access_mode VARCHAR NOT NULL DEFAULT 'read_only',
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_datasets (
                    id VARCHAR PRIMARY KEY,
                    pool_id VARCHAR,
                    name VARCHAR NOT NULL,
                    source_path TEXT,
                    source_host TEXT,
                    policy_id VARCHAR,
                    ingest_mode VARCHAR,
                    volume_group_id VARCHAR,
                    tape_set TEXT NOT NULL DEFAULT '[]',
                    shard_map TEXT NOT NULL DEFAULT '{}',
                    file_count INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    copies_completed INTEGER NOT NULL DEFAULT 0,
                    manifest_path TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_file_records (
                    id VARCHAR PRIMARY KEY,
                    dataset_id VARCHAR NOT NULL,
                    pool_id VARCHAR,
                    relative_path TEXT NOT NULL,
                    source_path TEXT,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    mtime TEXT,
                    checksum_sha256 TEXT,
                    tape_barcode TEXT,
                    tape_offset INTEGER,
                    status VARCHAR NOT NULL DEFAULT 'offline_on_tape',
                    cache_path TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS nas_restore_jobs (
                    id VARCHAR PRIMARY KEY,
                    status VARCHAR NOT NULL DEFAULT 'queued',
                    priority INTEGER NOT NULL DEFAULT 5,
                    paths TEXT NOT NULL DEFAULT '[]',
                    pool_id VARCHAR,
                    dataset_id VARCHAR,
                    destination TEXT NOT NULL DEFAULT '/openblade/restore',
                    allow_parallel INTEGER NOT NULL DEFAULT 1,
                    max_drives INTEGER NOT NULL DEFAULT 2,
                    cache_policy VARCHAR NOT NULL DEFAULT 'restore_to_destination',
                    overwrite_policy VARCHAR NOT NULL DEFAULT 'skip_existing',
                    required_tapes TEXT NOT NULL DEFAULT '[]',
                    missing_tapes TEXT NOT NULL DEFAULT '[]',
                    exported_tapes TEXT NOT NULL DEFAULT '[]',
                    tape_load_order TEXT NOT NULL DEFAULT '[]',
                    parallel_restore_groups TEXT NOT NULL DEFAULT '{}',
                    estimated_bytes INTEGER NOT NULL DEFAULT 0,
                    bytes_restored INTEGER NOT NULL DEFAULT 0,
                    files_restored INTEGER NOT NULL DEFAULT 0,
                    files_failed INTEGER NOT NULL DEFAULT 0,
                    partial_success INTEGER NOT NULL DEFAULT 0,
                    unavailable_files TEXT NOT NULL DEFAULT '[]',
                    warnings TEXT NOT NULL DEFAULT '[]',
                    error_message TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    completed_at TEXT
                )
                """
            )
        )


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


def get_catalog_repository() -> CatalogRepository:
    from openblade.bootstrap import get_context

    return get_context().catalog
