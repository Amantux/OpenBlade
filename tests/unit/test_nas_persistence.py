"""Persistence of the NAS domain models (pools, reservations, spans, export sets, vaults)."""

from __future__ import annotations

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
