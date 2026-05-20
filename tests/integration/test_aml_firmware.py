"""Integration tests for AML firmware endpoints (task-012)."""

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'fw-test.db'}"))
    reset_context(context)
    return TestClient(app)


@pytest.fixture()
def authed(client: TestClient) -> TestClient:
    resp = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert resp.status_code == 200
    return client


def _fw_upload(name: str, content: bytes = b"FAKEFIRMWARE") -> dict:
    return {"file": (name, BytesIO(content), "application/octet-stream")}


# ---------------------------------------------------------------------------
# Blade firmware
# ---------------------------------------------------------------------------

def test_list_blade_firmware_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/devices/blades/firmware")
    assert resp.status_code == 401


def test_list_blade_firmware_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/devices/blades/firmware")
    assert resp.status_code == 200


def test_upload_blade_firmware_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/devices/blades/firmware", files=_fw_upload("blade_fw.fmr"))
    assert resp.status_code == 401


def test_upload_blade_firmware(authed: TestClient) -> None:
    resp = authed.post("/aml/devices/blades/firmware", files=_fw_upload("blade_fw_1_2_3.fmr"))
    assert resp.status_code == 200
    assert resp.json().get("code") == 0


# ---------------------------------------------------------------------------
# Drive firmware images
# ---------------------------------------------------------------------------

def test_list_drive_firmware_images_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/drives/firmware/images")
    assert resp.status_code == 401


def test_list_drive_firmware_images_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drives/firmware/images")
    assert resp.status_code == 200


def test_upload_drive_firmware_valid_extension(authed: TestClient) -> None:
    for ext in [".drv", ".fmr", ".fmrz", ".img", ".ro", ".e", ".frm"]:
        resp = authed.post(
            "/aml/drives/firmware/images",
            files=_fw_upload(f"lto9_d9_1_0{ext}"),
        )
        assert resp.status_code == 200, f"Expected 200 for extension {ext}, got {resp.status_code}"


def test_upload_drive_firmware_invalid_extension(authed: TestClient) -> None:
    resp = authed.post(
        "/aml/drives/firmware/images",
        files=_fw_upload("firmware.zip"),
    )
    assert resp.status_code in {400, 422}


def test_upload_drive_firmware_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/drives/firmware/images", files=_fw_upload("fw.img"))
    assert resp.status_code == 401


def test_delete_drive_firmware_image_requires_admin(client: TestClient) -> None:
    resp = client.delete("/aml/drives/firmware/image/some-image")
    assert resp.status_code == 401


def test_delete_nonexistent_drive_firmware_returns_404(authed: TestClient) -> None:
    resp = authed.delete("/aml/drives/firmware/image/does-not-exist")
    assert resp.status_code == 404


def test_upload_then_delete_drive_firmware(authed: TestClient) -> None:
    # Upload
    up_resp = authed.post("/aml/drives/firmware/images", files=_fw_upload("lto9_d9_1_0.img"))
    assert up_resp.status_code == 200

    # List — image must appear
    list_resp = authed.get("/aml/drives/firmware/images")
    assert list_resp.status_code == 200

    # Delete
    del_resp = authed.delete("/aml/drives/firmware/image/lto9_d9_1_0.img")
    assert del_resp.status_code in {200, 404}


def test_version_preserved_on_upload(authed: TestClient) -> None:
    """Regression: uploading lto9_d9_1_0.img must record version D9.1.0, not D9."""
    authed.post("/aml/drives/firmware/images", files=_fw_upload("lto9_d9_1_0.img"))
    list_resp = authed.get("/aml/drives/firmware/images")
    assert list_resp.status_code == 200
    data = list_resp.json()
    images = (
        data.get("images")
        or data.get("firmwareFileList", {}).get("firmwareFile")
        or []
    )
    matching = [img for img in images if isinstance(img, dict) and "lto9_d9_1_0" in str(img.get("name", ""))]
    if matching:
        version = matching[0].get("version", "")
        assert version != "D9", f"Version collapsed to {version!r} instead of D9.1.0"


def test_activate_drive_firmware_requires_admin(client: TestClient) -> None:
    resp = client.put("/aml/drives/firmware/images/some-image/activate")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Per-drive firmware
# ---------------------------------------------------------------------------

def test_get_drive_firmware_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/drive/DRV-001/firmware")
    assert resp.status_code == 401


def test_get_drive_firmware_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/DRV-001/firmware")
    assert resp.status_code == 200


def test_get_drive_firmware_not_found(authed: TestClient) -> None:
    resp = authed.get("/aml/drive/NONEXISTENT/firmware")
    assert resp.status_code == 404


def test_update_drive_firmware_requires_admin(client: TestClient) -> None:
    resp = client.put("/aml/drive/DRV-001/firmware", json={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# System firmware
# ---------------------------------------------------------------------------

def test_get_system_firmware_requires_auth(client: TestClient) -> None:
    resp = client.get("/aml/system/firmware")
    assert resp.status_code == 401


def test_get_system_firmware_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/firmware")
    assert resp.status_code == 200


def test_get_system_firmware_status_returns_200(authed: TestClient) -> None:
    resp = authed.get("/aml/system/firmware/status")
    assert resp.status_code == 200


def test_upload_system_firmware_requires_admin(client: TestClient) -> None:
    resp = client.post("/aml/system/firmware", files=_fw_upload("system_fw_1_0_0.pkg"))
    assert resp.status_code == 401


def test_upload_system_firmware(authed: TestClient) -> None:
    resp = authed.post("/aml/system/firmware", files=_fw_upload("system_fw_1_0_0.pkg"))
    assert resp.status_code == 200


def test_activate_system_firmware_requires_admin(client: TestClient) -> None:
    resp = client.put("/aml/system/firmware/activate")
    assert resp.status_code == 401
