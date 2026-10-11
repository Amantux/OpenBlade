"""Share-push start: booking precedes plan registration; failures leave no orphans."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.capacity import ReservationError
from openblade.nas.ingest import ArchivePlanStore
from openblade.nas.service import NasService
from openblade.nas.types import NasPool, NasShareDefinition


class _Boom(RuntimeError):
    pass


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPENBLADE_STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("OPENBLADE_RESTORE_DIR", str(tmp_path / "restore"))
    reset_context(create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'p.db'}")))
    service = NasService(get_context().catalog)
    service.upsert_pool(NasPool(id="1", name="Pool 1", default_policy_id="balanced"))
    service.upsert_share(
        NasShareDefinition(
            path="/shares/pool-1",
            name="Pool 1 Share",
            share_type="pool",
            pool_ids=["1"],
            default_policy_id="balanced",
        )
    )
    return TestClient(app)


def _push(client: TestClient) -> Response:
    login = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    headers = {"Cookie": f"sessionID={login.cookies.get('sessionID')}"}
    upload = client.post(
        "/api/pools/1/upload",
        files={"file": ("push.txt", io.BytesIO(b"payload"), "text/plain")},
        headers=headers,
    )
    assert upload.status_code == 200
    return client.post(
        "/api/pools/1/push-to-share",
        json={"share_path": "/shares/pool-1", "file_ids": [upload.json()["file_id"]]},
        headers=headers,
    )


def _owners() -> set[str]:
    rows = get_context().catalog.reservations_for_pool("1", now=datetime.now(UTC))
    return {str(r["owner"]) for r in rows}


def test_share_push_refused_booking_is_409_and_registers_no_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The preview passes (budget free) but a concurrent start wins the booking.
    def _refuse(*_: object, **__: object) -> None:
        raise ReservationError("pool 1 cannot reserve capacity")

    monkeypatch.setattr("openblade.api.routes_upload.book_plan_capacity", _refuse)
    plans_before = set(ArchivePlanStore)

    response = _push(client)

    assert response.status_code == 409
    assert set(ArchivePlanStore) == plans_before


def test_share_push_start_failure_releases_booking_and_plan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(**_: object) -> None:
        raise _Boom("start failed")

    monkeypatch.setattr("openblade.api.routes_upload.start_ingest_job", _raise)
    plans_before = set(ArchivePlanStore)

    with pytest.raises(_Boom):
        _push(client)

    assert set(ArchivePlanStore) == plans_before
    assert _owners() == set()
