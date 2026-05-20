"""Integration tests for AML media enrichment and pool assignment routes."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'media-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


def test_media_detail_includes_capacity_usage_fields(authed: TestClient) -> None:
    response = authed.get("/aml/media/VOL001L9")
    assert response.status_code == 200

    media = response.json()["media"]
    assert media["capacityGB"] == 18000
    assert 0 < media["usedGB"] <= media["capacityGB"]
    assert 20 <= media["percentUsed"] <= 85
    assert media["poolName"] is None


def test_seeded_media_pools_include_ui_fields(authed: TestClient) -> None:
    response = authed.get("/aml/media/pools")
    assert response.status_code == 200

    pools = {item["id"]: item for item in response.json()["poolList"]["pool"]}
    assert {"default", "cleaning", "pool-critical", "pool-general", "pool-cold"}.issubset(pools)
    assert pools["pool-critical"]["name"] == "Critical Backups"
    assert pools["pool-critical"]["policy"] == "critical"
    assert pools["pool-critical"]["maxDrives"] == 2
    assert pools["pool-critical"]["targetLtoGeneration"] == "LTO-9"
    assert pools["pool-critical"]["quotaGB"] == 50000
    assert pools["pool-critical"]["color"] == "#EF4444"
    assert pools["pool-critical"]["assignedBarcodes"] == []


def test_assign_and_unassign_pool_updates_media_pool_name(authed: TestClient) -> None:
    create_response = authed.post(
        "/aml/media/pools",
        json={
            "name": "Archive A",
            "policy": "archive",
            "maxDrives": 2,
            "targetLtoGeneration": "LTO-9",
            "quotaGB": 24000,
            "color": "#7C3AED",
        },
    )
    assert create_response.status_code == 201

    pool = create_response.json()["pool"]
    assert pool["id"] == "archive-a"
    assert pool["assignedBarcodes"] == []
    assert pool["maxDrives"] == 2
    assert pool["targetLtoGeneration"] == "LTO-9"

    assign_response = authed.post(
        "/aml/media/pools/archive-a/assign",
        json={"barcodes": ["VOL001L9"]},
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["pool"]["mediaCount"] == 1
    assert assign_response.json()["pool"]["assignedBarcodes"] == ["VOL001L9"]

    media_response = authed.get("/aml/media/VOL001L9")
    assert media_response.status_code == 200
    assert media_response.json()["media"]["poolName"] == "Archive A"

    unassign_response = authed.delete("/aml/media/pools/archive-a/assign/VOL001L9")
    assert unassign_response.status_code == 200
    assert unassign_response.json()["pool"]["mediaCount"] == 0
    assert unassign_response.json()["pool"]["assignedBarcodes"] == []

    media_response = authed.get("/aml/media/VOL001L9")
    assert media_response.status_code == 200
    assert media_response.json()["media"]["poolName"] is None


def test_update_media_pool_allows_clearing_nullable_fields(authed: TestClient) -> None:
    response = authed.put(
        "/aml/media/pools/pool-critical",
        json={"quotaGB": None, "targetLtoGeneration": None},
    )
    assert response.status_code == 200

    pool = response.json()["pool"]
    assert pool["quotaGB"] is None
    assert pool["targetLtoGeneration"] is None

    get_response = authed.get("/aml/media/pools/pool-critical")
    assert get_response.status_code == 200
    pool = get_response.json()["pool"]
    assert pool["quotaGB"] is None
    assert pool["targetLtoGeneration"] is None
