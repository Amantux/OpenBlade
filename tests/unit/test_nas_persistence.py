"""Persistence of the NAS domain models (pools, reservations, spans, export sets, vaults)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

from openblade.catalog.db import _migrate_schema, get_session, init_db
from openblade.catalog.repository import CatalogRepository


@pytest.fixture
def repo(tmp_path: Path) -> CatalogRepository:
    init_db(f"sqlite:///{tmp_path / 'catalog.db'}")
    return CatalogRepository(get_session())


_OLD_NAS_POOLS_DDL = """
CREATE TABLE nas_pools (
    id VARCHAR PRIMARY KEY,
    name VARCHAR(64) NOT NULL UNIQUE,
    description TEXT,
    volume_group_ids TEXT,
    default_policy_id VARCHAR,
    default_ingest_mode VARCHAR,
    mount_path VARCHAR,
    virtual_mount_enabled BOOLEAN,
    hydration_behavior VARCHAR,
    cache_target_id VARCHAR,
    restore_target_path VARCHAR,
    access_mode VARCHAR,
    created_at TEXT,
    updated_at TEXT
)
"""

_NEW_TABLES = {
    "nas_reservations",
    "nas_export_sets",
    "nas_vaults",
    "nas_vg_replication",
    "nas_file_spans",
}


def test_migration_upgrades_pre_change_db_idempotently(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text(_OLD_NAS_POOLS_DDL))
        connection.execute(text("INSERT INTO nas_pools (id, name) VALUES ('p1', 'legacy')"))

    _migrate_schema(engine)
    _migrate_schema(engine)  # second run must be a no-op, not an error

    inspector = inspect(engine)
    pool_columns = {column["name"] for column in inspector.get_columns("nas_pools")}
    assert {"replication_factor", "protection_json"} <= pool_columns
    assert set(inspector.get_table_names()) >= _NEW_TABLES
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT replication_factor, protection_json FROM nas_pools WHERE id = 'p1'")
        ).one()
    assert row == (1, None)


def test_pool_round_trips_replication_and_protection(repo: CatalogRepository) -> None:
    saved = repo.upsert_nas_pool({"id": "p1", "name": "pool-a", "replication_factor": 2})
    assert saved["replication_factor"] == 2
    assert saved["protection"] is None
    loaded = repo.get_nas_pool("p1")
    assert loaded is not None
    assert loaded["replication_factor"] == 2
    # Round trip: the dict form must be accepted back by upsert unchanged.
    again = repo.upsert_nas_pool(dict(loaded))
    assert again["replication_factor"] == 2


def test_pool_protection_is_serialised_like_dataset_protection(repo: CatalogRepository) -> None:
    import json

    from openblade.domain.protection import ProtectionPolicy

    policy = ProtectionPolicy()
    saved = repo.upsert_nas_pool({"id": "p2", "name": "pool-b", "protection": policy})
    assert saved["protection"] == json.loads(json.dumps(policy.to_dict(), sort_keys=True))
    again = repo.upsert_nas_pool(dict(saved))
    assert again["protection"] == saved["protection"]


_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_reservations_round_trip_and_expiry_filter(repo: CatalogRepository) -> None:
    live = repo.upsert_nas_reservation(
        {"pool_id": "p1", "bytes": 100, "owner": "job-1", "expires_at": _NOW + timedelta(hours=1)}
    )
    repo.upsert_nas_reservation(
        {"pool_id": "p1", "bytes": 50, "owner": "job-2", "expires_at": _NOW}
    )
    repo.upsert_nas_reservation(
        {"pool_id": "p2", "bytes": 7, "owner": "job-3", "expires_at": _NOW + timedelta(days=1)}
    )
    assert live["expires_at"] == _NOW + timedelta(hours=1)
    assert len(repo.list_nas_reservations("p1")) == 2
    active = repo.reservations_for_pool("p1", now=_NOW)
    assert [item["owner"] for item in active] == ["job-1"]
    assert repo.delete_nas_reservation(str(live["id"])) is True
    assert repo.reservations_for_pool("p1", now=_NOW) == []
    assert repo.delete_nas_reservation(str(live["id"])) is False


def test_export_sets_vaults_and_vg_replication_round_trip(repo: CatalogRepository) -> None:
    repo.upsert_nas_export_set({"id": "e1", "barcodes": ["A00001L9"], "state": "pending"})
    repo.upsert_nas_export_set({"id": "e1", "barcodes": ["A00001L9", "A00002L9"], "state": "out"})
    assert repo.list_nas_export_sets() == [
        {"id": "e1", "barcodes": ["A00001L9", "A00002L9"], "state": "out"}
    ]
    repo.upsert_nas_vault({"id": "v1", "barcodes": ["A00003L9"], "location": "offsite"})
    assert repo.list_nas_vaults() == [{"id": "v1", "barcodes": ["A00003L9"], "location": "offsite"}]
    repo.upsert_nas_vg_replication(
        {"vg_id": "vg1", "barcodes": ["A"], "replicas_required": 2, "replicas_present": 1}
    )
    assert repo.list_nas_vg_replication() == [
        {"vg_id": "vg1", "barcodes": ["A"], "replicas_required": 2, "replicas_present": 1}
    ]
    assert repo.delete_nas_export_set("e1")
    assert repo.delete_nas_vault("v1")
    assert repo.delete_nas_vg_replication("vg1")
    assert repo.list_nas_export_sets() == repo.list_nas_vaults() == []
    assert repo.list_nas_vg_replication() == []


def test_file_spans_and_scratch_thresholds_round_trip(repo: CatalogRepository) -> None:
    segments = [
        {"barcode": "A00001L9", "offset": 0, "length": 10},
        {"barcode": "A00002L9", "offset": 0, "length": 5},
    ]
    span = repo.upsert_nas_file_span({"path": "/big.bin", "segments": segments})
    repo.upsert_nas_file_span({"path": "/other.bin", "segments": segments[:1]})
    assert repo.list_nas_file_spans("/big.bin") == [
        {"id": span["id"], "path": "/big.bin", "segments": segments}
    ]
    assert len(repo.list_nas_file_spans()) == 2
    assert repo.delete_nas_file_span(str(span["id"]))
    assert repo.get_scratch_thresholds() is None
    repo.set_scratch_thresholds(2, 5)
    assert repo.get_scratch_thresholds() == {"scratch_min": 2, "scratch_warn": 5}
