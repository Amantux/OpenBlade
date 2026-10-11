"""Start-path capacity safety: pool match, booking before registration, rollback."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.ingest import get_archive_plan

client = TestClient(app)


@pytest.fixture(autouse=True)
def _context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 's.db'}")))
    for pool in ("pool-1", "pool-2"):
        created = client.post("/nas/pools", json={"id": pool, "name": pool})
        assert created.status_code in {200, 201}
    monkeypatch.setattr("openblade.api.nas_config.run_ingest_job", lambda *a, **k: None)


def _preview(pool: str | None) -> dict[str, object]:
    body: dict[str, object] = {
        "files": ["a.bin"],
        "file_sizes": {"a.bin": 1_000},
        "ingest_mode": "source_stream",
    }
    if pool is None:
        body.update({"available_tapes": ["T1"], "tape_capacities": {"T1": 10**12}})
    else:
        body["pool"] = pool
    plan: dict[str, object] = client.post("/nas/archive-plan", json=body).json()
    assert plan["is_safe_to_enqueue"], plan
    return plan


def _start(plan_id: object, pool_id: str | None) -> Response:
    body = {"plan_id": plan_id, "dataset_name": "ds", "auto_clean_drives": False}
    return client.post("/nas/ingest/start", json={**body, "pool_id": pool_id})


def _owners(pool: str) -> set[str]:
    rows = get_context().catalog.reservations_for_pool(pool, now=datetime.now(UTC))
    return {str(r["owner"]) for r in rows}


def test_start_into_a_different_pool_is_409_and_books_nothing() -> None:
    plan = _preview("pool-1")

    response = _start(plan["plan_id"], "pool-2")

    assert response.status_code == 409
    assert response.json()["detail"] == "pool_id does not match plan pool"
    assert _owners("pool-1") == set()
    assert _owners("pool-2") == set()


def test_poolless_preview_cannot_start_into_a_pool() -> None:
    plan = _preview(None)

    response = _start(plan["plan_id"], "pool-1")

    assert response.status_code == 409
    assert _owners("pool-1") == set()


def test_matching_pool_starts_and_dataset_uses_the_plan_pool() -> None:
    from openblade.api.nas_config import get_nas_service

    plan = _preview("pool-1")

    response = _start(plan["plan_id"], "pool-1")

    assert response.status_code == 200, response.text
    dataset_id = response.json()["dataset_id"]
    dataset = get_nas_service(get_context().catalog).get_dataset(dataset_id)
    assert dataset is not None and dataset.pool_id == "pool-1"
    assert _owners("pool-1") == {plan["plan_id"]}
    assert get_archive_plan(str(plan["plan_id"])) is not None
