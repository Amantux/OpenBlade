"""Tests for disaster-recovery backup + restore verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from openblade.catalog.db import init_db
from openblade.dr import backup_sqlite, restore_and_verify, sqlite_path


def _seed_db(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'source.db'}"
    init_db(url)
    return url


def test_backup_then_restore_verify_passes(tmp_path: Path) -> None:
    src_url = _seed_db(tmp_path)
    backup = tmp_path / "backup.db"
    meta = backup_sqlite(src_url, backup)
    assert meta["bytes"] > 0 and meta["sha256"]

    report = restore_and_verify(backup)
    assert report.ok, report.to_dict()
    names = {c.name: c.ok for c in report.checks}
    assert names["integrity_check"] and names["schema_present"] and names["data_layer_query"]


def test_restore_detects_corrupt_backup(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"this is not a sqlite database" * 10)

    report = restore_and_verify(corrupt)

    assert not report.ok  # the "restore contains incomplete/corrupt data" failure


def test_restore_detects_incomplete_backup(tmp_path: Path) -> None:
    # A valid SQLite file (passes integrity_check) that is missing the core catalog
    # tables must NOT be greenlit — restore must not recreate them to mask the loss.
    import sqlite3

    incomplete = tmp_path / "incomplete.db"
    with sqlite3.connect(str(incomplete)) as conn:
        conn.execute("CREATE TABLE nas_datasets (id INTEGER PRIMARY KEY)")

    report = restore_and_verify(incomplete)

    assert not report.ok
    names = {c.name: c.ok for c in report.checks}
    assert names.get("integrity_check")  # it IS a valid sqlite file
    assert not names.get("schema_present")  # ...but missing file_records/cartridges/jobs
    assert not names.get("data_layer_query")


def test_restore_does_not_touch_global_catalog_engine(tmp_path: Path) -> None:
    from openblade import catalog

    live_url = _seed_db(tmp_path)
    catalog.db.init_db(live_url)  # establish a live global engine
    before = catalog.db._db_url

    backup = tmp_path / "backup.db"
    backup_sqlite(live_url, backup)
    restore_and_verify(backup)

    assert catalog.db._db_url == before  # global engine unchanged by the DR check


def test_restore_missing_backup_fails(tmp_path: Path) -> None:
    report = restore_and_verify(tmp_path / "does-not-exist.db")
    assert not report.ok
    assert any(c.name == "backup_present" and not c.ok for c in report.checks)


def test_cannot_backup_memory_db() -> None:
    with pytest.raises(ValueError):
        backup_sqlite("sqlite:///:memory:", Path("/tmp/x.db"))


def test_sqlite_path_parsing() -> None:
    assert sqlite_path("sqlite:////data/openblade.db") == "/data/openblade.db"
    assert sqlite_path("sqlite:///rel/openblade.db") == "rel/openblade.db"
