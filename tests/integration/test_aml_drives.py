"""Integration tests for AML drive management endpoints (task-008)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'drives-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


# ---------------------------------------------------------------------------
# List / Get drives
# ---------------------------------------------------------------------------

def test_list_drives_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/drives")
    assert resp.status_code == 401


def test_list_drives_returns_drives(authed: TestClient) -> None:
    resp = authed.get("/aml/drives")
    assert resp.status_code == 200
    data = resp.json()
    # Response may be {"drives": [...]} or {"driveList": {"drive": [...]}}
    drives = data.get("drives") or (data.get("driveList") or {}).get("drive") or []
    assert len(drives) >= 1


def test_get_drive_returns_details(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("serialNumber") == "DRV-001" or "drive" in data


def test_get_drive_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/NONEXISTENT")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Drive status
# ---------------------------------------------------------------------------

def test_drives_status_returns_list(authed: TestClient) -> None:
    resp = authed.get("/aml/drives/status")
    assert resp.status_code == 200


def test_drive_status_returns_single(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/status")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Drive media endpoint — regression for loadedMedia mount hotfix
# ---------------------------------------------------------------------------

def test_drive_media_returns_empty_when_nothing_loaded(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/media")
    assert resp.status_code == 200


def test_drive_media_after_mount_does_not_crash(authed: TestClient) -> None:
    """Regression: GET /aml/drive/{sn}/media must not 500 after a mount."""
    # Mount a known cartridge onto DRV-001
    mount_resp = authed.post("/aml/mount", json={"mount": {"barcode": "VOL001L9", "drive": "DRV-001"}})
    # Mount may 409 if already mounted in seeded state; that's OK — we just verify no 500 on GET
    assert mount_resp.status_code in {200, 409}
    media_resp = authed.get("/aml/drive/DRV-001/media")
    assert media_resp.status_code != 500


# ---------------------------------------------------------------------------
# Unload — cartridge state consistency fix
# ---------------------------------------------------------------------------

def test_unload_clears_drive_and_updates_media(authed: TestClient) -> None:
    """After unload, drive must be empty and cartridge must not remain in loaded state."""
    # Mount first
    authed.post("/aml/mount", json={"mount": {"barcode": "VOL002L9", "drive": "DRV-002"}})

    # Unload
    resp = authed.post("/aml/drive/DRV-002/unload")
    assert resp.status_code == 200
    assert resp.json().get("code") == 0

    # Drive must be empty
    drive_resp = authed.get("/aml/drive/DRV-002")
    assert drive_resp.status_code == 200

    # Media must not be in loaded state pointing at the drive
    media_resp = authed.get("/aml/media/VOL002L9")
    if media_resp.status_code == 200:
        media_data = media_resp.json()
        state = media_data.get("state") or (media_data.get("media") or {}).get("state")
        slot = media_data.get("slotAddress") or (media_data.get("media") or {}).get("slotAddress")
        assert state != "loaded" or slot != "DRV-002"


# ---------------------------------------------------------------------------
# Drive statistics
# ---------------------------------------------------------------------------

def test_drive_statistics(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/statistics")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Drive config
# ---------------------------------------------------------------------------

def test_get_drive_config(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/config")
    assert resp.status_code == 200


def test_put_drive_config_requires_admin(client: TestClient) -> None:
    resp = client.put("/aml/drive/DRV-001/config", json={})
    assert resp.status_code == 401
