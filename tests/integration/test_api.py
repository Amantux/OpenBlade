from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'api.db'}"))
    reset_context(context)


def _format_first_tape() -> tuple[str, str]:
    from openblade.bootstrap import get_context

    context = get_context()
    barcode = context.catalog.list_cartridges()[0].barcode
    plan = client.post(f"/cartridges/{barcode}/format/dry-run")
    return barcode, plan.json()["token"]


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_get_inventory() -> None:
    response = client.get("/inventory/")
    assert response.status_code == 200
    assert response.json()["library_id"] == "mock-i3-001"


def test_create_volume_group() -> None:
    response = client.post("/volume-groups/", json={"name": "photos"})
    assert response.status_code == 201
    assert response.json()["name"] == "photos"


def test_create_duplicate_volume_group_returns_409() -> None:
    assert client.post("/volume-groups/", json={"name": "photos"}).status_code == 201
    response = client.post("/volume-groups/", json={"name": "photos"})
    assert response.status_code == 409


def test_get_volume_groups() -> None:
    client.post("/volume-groups/", json={"name": "photos"})
    response = client.get("/volume-groups/")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "photos"


def test_archive_returns_job_id(tmp_path: Path) -> None:
    barcode, token = _format_first_tape()
    client.post("/volume-groups/", json={"name": "photos"})
    client.post("/volume-groups/photos/assign", json={"barcode": barcode})
    client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token})
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("api archive")
    response = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    assert response.status_code == 202
    assert response.json()["job_id"]


def test_restore_returns_job_id(tmp_path: Path) -> None:
    barcode, token = _format_first_tape()
    client.post("/volume-groups/", json={"name": "photos"})
    client.post("/volume-groups/photos/assign", json={"barcode": barcode})
    client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token})
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("api restore")
    archive = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    response = client.post(
        "/restore/",
        json={"catalog_path": "/photos/a.txt", "dest_path": str(restore_dir)},
    )
    assert archive.status_code == 202
    assert response.status_code == 202
    assert response.json()["job_id"]


def test_get_job_status(tmp_path: Path) -> None:
    barcode, token = _format_first_tape()
    client.post("/volume-groups/", json={"name": "photos"})
    client.post("/volume-groups/photos/assign", json={"barcode": barcode})
    client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token})
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("job status")
    create = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    response = client.get(f"/jobs/{create.json()['job_id']}")
    assert response.status_code == 200
    assert response.json()["job_type"] == "archive"


def test_get_nonexistent_job_returns_404() -> None:
    response = client.get("/jobs/missing")
    assert response.status_code == 404


def test_format_dry_run() -> None:
    barcode = client.get("/cartridges/").json()[0]["barcode"]
    response = client.post(f"/cartridges/{barcode}/format/dry-run")
    assert response.status_code == 200
    assert response.json()["token"]


def test_format_confirm_requires_token() -> None:
    barcode = client.get("/cartridges/").json()[0]["barcode"]
    response = client.post(
        "/cartridges/format/confirm", json={"barcode": barcode, "token": "bad-token"}
    )
    assert response.status_code == 400


def test_invalid_archive_request_returns_422() -> None:
    response = client.post("/archive/", json={"volume_group": "photos"})
    assert response.status_code == 422


def test_sharded_archive_returns_job_id(tmp_path: Path) -> None:
    barcode, token = _format_first_tape()
    client.post("/volume-groups/", json={"name": "photos"})
    client.post("/volume-groups/photos/assign", json={"barcode": barcode})
    client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token})
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("api sharded archive")
    response = client.post(
        "/archive/sharded",
        json={
            "source_path": str(source),
            "volume_group": "photos",
            "lane_barcodes": [barcode],
            "mode": "stripe",
            "block_size_mb": 1,
        },
    )
    assert response.status_code == 202
    assert response.json()["job_id"]
