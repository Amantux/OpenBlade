from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from openblade.catalog.db import get_session, init_db
from openblade.catalog.repository import CatalogRepository

pytestmark = pytest.mark.real_hardware


EXPECTED_TABLES = {"cartridges", "file_records", "file_instances", "jobs", "volume_groups"}


def _sqlite_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _catalog(path: Path) -> CatalogRepository:
    init_db(_sqlite_url(path))
    return CatalogRepository(get_session())


def test_catalog_schema_accessible(real_hardware_guard, default_db_path, runner):
    """Requires: a SQLite-backed catalog database path."""
    del runner
    catalog = _catalog(default_db_path)
    del catalog
    with sqlite3.connect(default_db_path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(table_names)


def test_no_duplicate_file_records(
    real_hardware_guard,
    scratch_barcodes,
    real_app_context,
    tmp_path,
):
    """Requires: real archive service and SQLite-backed catalog."""
    if not hasattr(real_app_context, "archive_service"):
        pytest.skip("Real AppContext archive support is not implemented")
    group = real_app_context.catalog.get_volume_group("hw-dedup") or real_app_context.catalog.create_volume_group("hw-dedup")
    for barcode in scratch_barcodes:
        real_app_context.catalog.add_barcode_to_volume_group(group.id, barcode)
    source_dir = tmp_path / "dedup-source"
    source_dir.mkdir()
    file_path = source_dir / "same.bin"
    file_path.write_bytes(b"same-content")
    real_app_context.archive_service.enqueue("hw-dedup", source_dir)
    real_app_context.archive_service.enqueue("hw-dedup", source_dir)
    records = real_app_context.catalog.list_file_records(f"/hw-dedup/{file_path.name}")
    unique = {(record.path, record.checksum_sha256) for record in records}
    assert len(unique) <= 1


def test_volume_group_foreign_key(real_hardware_guard, default_db_path, runner):
    """Requires: a SQLite-backed catalog database path."""
    del runner
    _catalog(default_db_path)
    with sqlite3.connect(default_db_path) as connection:
        rows = connection.execute(
            """
            SELECT COUNT(*)
            FROM cartridges c
            LEFT JOIN volume_groups vg ON vg.id = c.volume_group_id
            WHERE c.volume_group_id IS NOT NULL AND vg.id IS NULL
            """
        ).fetchone()
    assert rows is not None and rows[0] == 0


def test_concurrent_catalog_reads(real_hardware_guard, default_db_path, runner):
    """Requires: a SQLite-backed catalog database path."""
    del runner
    _catalog(default_db_path)
    errors = []

    def _reader() -> None:
        try:
            with sqlite3.connect(default_db_path) as connection:
                connection.execute("SELECT COUNT(*) FROM jobs").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_reader) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)
    assert errors == []


def test_job_status_transitions(real_hardware_guard, default_db_path, runner):
    """Requires: a SQLite-backed catalog database path."""
    del runner
    catalog = _catalog(default_db_path)
    job = catalog.create_job("archive", {"path": "/data/demo"})
    states = [job.state]
    running = catalog.get_job(job.id)
    assert running is not None
    catalog.update_job_state(job.id, "running")
    running = catalog.get_job(job.id)
    assert running is not None
    states.append(running.state)
    catalog.update_job_state(job.id, "completed")
    completed = catalog.get_job(job.id)
    assert completed is not None
    states.append(completed.state)
    assert states == ["pending", "running", "completed"]
