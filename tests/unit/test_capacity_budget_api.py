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
