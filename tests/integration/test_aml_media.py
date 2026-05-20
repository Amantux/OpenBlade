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


def test_assign_and_unassign_pool_updates_media_pool_name(authed: TestClient) -> None:
    create_response = authed.post("/aml/media/pool/archive-a", json={"pool": {"type": "LTO-9", "policy": "archive"}})
    assert create_response.status_code == 201

    assign_response = authed.post(
        "/aml/media/pool/archive-a/assign",
        json={"barcodeList": {"barcode": ["VOL001L9"]}},
    )
    assert assign_response.status_code == 200
    assert assign_response.json()["pool"]["mediaCount"] == 1

    media_response = authed.get("/aml/media/VOL001L9")
    assert media_response.status_code == 200
    assert media_response.json()["media"]["poolName"] == "archive-a"

    unassign_response = authed.post(
        "/aml/media/pool/archive-a/unassign",
        json={"barcodeList": {"barcode": ["VOL001L9"]}},
    )
    assert unassign_response.status_code == 200
    assert unassign_response.json()["pool"]["mediaCount"] == 0

    media_response = authed.get("/aml/media/VOL001L9")
    assert media_response.status_code == 200
    assert media_response.json()["media"]["poolName"] is None
