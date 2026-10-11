"""API-level guards: a pool-budgeted plan's capacity inputs come from the server only."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.planner import POOL_BUDGET_BLOCKER

client = TestClient(app)


@pytest.fixture(autouse=True)
def _context(tmp_path: Path) -> None:
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'b.db'}")))
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "P1"}).status_code in {200, 201}


def _body(size: int, **extra: object) -> dict[str, object]:
    return {"files": ["a.bin"], "file_sizes": {"a.bin": size}, "pool": "pool-1", **extra}


@pytest.mark.parametrize(
    "field",
    [
        {"tape_capacities": {"T1": 10**18}},
        {"available_tapes": ["T1"]},
        {"reserved_bytes": {"T1": 0}},
        {"pool_reserved_bytes": 0},
    ],
)
def test_client_capacity_inputs_with_pool_are_refused(field: dict[str, object]) -> None:
    response = client.post("/nas/archive-plan", json=_body(1, **field))
    assert response.status_code == 422
    assert "server derives" in response.json()["detail"]


def test_unknown_pool_is_404() -> None:
    response = client.post("/nas/archive-plan", json={**_body(1), "pool": "nope"})
    assert response.status_code == 404


def test_honest_over_budget_is_blocked_by_server_capacity() -> None:
    plan = client.post("/nas/archive-plan", json=_body(10**18)).json()
    assert POOL_BUDGET_BLOCKER in plan["enqueue_blockers"]


def test_poolless_request_keeps_body_inputs_and_no_budget() -> None:
    body = {
        "files": ["a.bin"],
        "file_sizes": {"a.bin": 10},
        "available_tapes": ["T1"],
        "tape_capacities": {"T1": 100},
    }
    plan = client.post("/nas/archive-plan", json=body).json()
    assert POOL_BUDGET_BLOCKER not in plan["enqueue_blockers"]


def test_preview_books_nothing_but_second_start_is_409(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime, timedelta

    from openblade.bootstrap import get_context
    from openblade.nas.capacity import server_data_tapes

    # Arrange: keep started jobs running (no release) and leave 1500 bytes of pool budget.
    monkeypatch.setattr("openblade.api.nas_config.run_ingest_job", lambda *a, **k: None)
    context = get_context()
    repo = context.catalog
    capacity = sum(server_data_tapes(repo, context.library).values())
    now = datetime.now(UTC)
    assert repo.reserve_nas_capacity(
        "pool-1",
        capacity - 1_500,
        "other-job",
        now + timedelta(hours=1),
        capacity_bytes=capacity,
        now=now,
    )

    # Act: two previews of 1000 bytes each, then start both.
    first = client.post("/nas/archive-plan", json=_body(1_000, ingest_mode="source_stream")).json()
    second = client.post("/nas/archive-plan", json=_body(1_000, ingest_mode="source_stream")).json()
    start = {"dataset_name": "ds", "pool_id": "pool-1", "auto_clean_drives": False}
    first_start = client.post("/nas/ingest/start", json={**start, "plan_id": first["plan_id"]})
    second_start = client.post("/nas/ingest/start", json={**start, "plan_id": second["plan_id"]})

    # Assert: previews never consumed budget; the second START lost to the first booking.
    assert first["is_safe_to_enqueue"] and second["is_safe_to_enqueue"]
    assert first_start.status_code == 200, first_start.text
    assert second_start.status_code == 409
    owners = {r["owner"] for r in repo.reservations_for_pool("pool-1", now=now)}
    assert owners == {"other-job", first["plan_id"]}
