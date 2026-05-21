from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'dashboard.db'}"))
    reset_context(context)
    return TestClient(app)


def _first_data_barcode() -> str:
    context = get_context()
    return next(
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    )


def _admin_auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def _prepare_archive(client: TestClient, tmp_path: Path) -> int:
    barcode = _first_data_barcode()
    auth_headers = _admin_auth_headers(client)
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run", headers=auth_headers)
    assert dry_run.status_code == 200
    token = dry_run.json()["token"]
    assert client.post("/volume-groups/", json={"name": "photos"}).status_code == 201
    assert client.post(f"/volume-groups/photos/assign", json={"barcode": barcode}).status_code == 200
    assert (
        client.post(
            "/cartridges/format/confirm",
            json={"barcode": barcode, "token": token},
            headers={**auth_headers, "X-Openblade-Service-Token": "openblade-controller-dev-token-do-not-expose"},
        ).status_code
        == 200
    )

    source = tmp_path / "source"
    source.mkdir()
    archived_file = source / "stats.txt"
    archived_file.write_text("dashboard smoke")

    archive_response = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    assert archive_response.status_code == 202
    return archived_file.stat().st_size


def test_dashboard_stats_smoke_reflects_seeded_library_state(client: TestClient, tmp_path: Path) -> None:
    archived_size = _prepare_archive(client, tmp_path)
    context = get_context()
    inventory = context.library.inventory()

    response = client.get("/dashboard/stats")
    assert response.status_code == 200
    payload = response.json()

    assert payload["drive_count"] == len(inventory.drives)
    assert payload["slot_count"] == len(inventory.slots)
    assert payload["job_count"] >= 1
    assert payload["event_count"] >= 1
    assert payload["pool_count"] >= 1
    assert payload["storage"]["totalFiles"] == 1
    assert payload["storage"]["totalBytes"] == archived_size
    assert payload["storage"]["volumeGroupCount"] == 1
    assert payload["storage"]["totalAssignedTapes"] == 1
    assert payload["storage"]["totalCatalogTapes"] == 1
    assert payload["volumeGroups"][0]["name"] == "photos"
    assert payload["volumeGroups"][0]["fileCount"] == 1
