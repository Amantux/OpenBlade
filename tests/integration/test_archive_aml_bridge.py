from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'archive-aml-bridge.db'}"))
    reset_context(context)
    return TestClient(app)


def _login_admin(client: TestClient) -> None:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def _first_data_barcode() -> str:
    context = get_context()
    return next(
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    )


def _prepare_volume_group(client: TestClient, volume_group: str = "photos") -> str:
    barcode = _first_data_barcode()
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run")
    assert dry_run.status_code == 200
    token = dry_run.json()["token"]
    assert client.post("/volume-groups/", json={"name": volume_group}).status_code == 201
    assert client.post(f"/volume-groups/{volume_group}/assign", json={"barcode": barcode}).status_code == 200
    assert client.post("/cartridges/format/confirm", json={"barcode": barcode, "token": token}).status_code == 200
    return barcode


def test_archive_bridges_into_aml_jobs_events_and_catalog(client: TestClient, tmp_path: Path) -> None:
    _login_admin(client)
    _prepare_volume_group(client)

    source = tmp_path / "source"
    source.mkdir()
    archived_file = source / "a.txt"
    archived_file.write_text("archive aml bridge")

    response = client.post("/archive/", json={"source_path": str(source), "volume_group": "photos"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    jobs_response = client.get("/aml/jobs")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["jobList"]["job"]
    aml_job = next(job for job in jobs if job["id"] == job_id)
    assert aml_job["type"] == "archive"
    assert aml_job["status"] == "completed"

    events_response = client.get("/aml/events")
    assert events_response.status_code == 200
    events = events_response.json()["eventList"]["event"]
    assert any(
        event["component"] == "archive"
        and event["details"].get("jobId") == job_id
        and "/photos" in event["message"]
        for event in events
    )

    catalog_response = client.get("/catalog/")
    assert catalog_response.status_code == 200
    payload = catalog_response.json()
    assert any(file["source_path"] == "/photos/a.txt" for file in payload["files"])

    drive_response = client.get("/aml/drive/DRV-001")
    assert drive_response.status_code == 200
    drive = drive_response.json().get("drive") or drive_response.json()
    assert drive["state"] == "idle"
    assert drive["loadedMedia"] is None
    assert archived_file.stat().st_size > 0


def test_restore_bridges_into_aml_jobs_and_events(client: TestClient, tmp_path: Path) -> None:
    _login_admin(client)
    _prepare_volume_group(client)

    source = tmp_path / "source"
    source.mkdir()
    archived_file = source / "restore.txt"
    archived_file.write_text("restore aml bridge")

    archive_response = client.post("/archive/", json={"source_path": str(source), "volume_group": "photos"})
    assert archive_response.status_code == 202

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    restore_response = client.post(
        "/restore/",
        json={"catalog_path": "/photos/restore.txt", "dest_path": str(restore_dir)},
    )
    assert restore_response.status_code == 202
    job_id = restore_response.json()["job_id"]

    jobs_response = client.get("/aml/jobs")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["jobList"]["job"]
    aml_job = next(job for job in jobs if job["id"] == job_id)
    assert aml_job["type"] == "restore"
    assert aml_job["status"] == "completed"

    events_response = client.get("/aml/events")
    assert events_response.status_code == 200
    events = events_response.json()["eventList"]["event"]
    assert any(
        event["component"] == "restore"
        and event["details"].get("jobId") == job_id
        and "/photos/restore.txt" in event["message"]
        for event in events
    )

    restored_file = restore_dir / "restore.txt"
    assert restored_file.read_text() == archived_file.read_text()
