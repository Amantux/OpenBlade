from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'archive-regression.db'}"))
    reset_context(context)
    return TestClient(app, raise_server_exceptions=False)


def _login_admin(client: TestClient) -> dict[str, str]:
    response = client.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200
    session_id = response.cookies.get("sessionID")
    assert session_id is not None
    return {"Cookie": f"sessionID={session_id}"}


def _data_barcodes(limit: int) -> list[str]:
    context = get_context()
    return [
        str(slot.barcode)
        for slot in context.library.inventory().slots
        if slot.barcode is not None and not str(slot.barcode).startswith("CLN")
    ][:limit]


def _prepare_volume_group(client: TestClient, name: str, barcode: str) -> None:
    auth_headers = _login_admin(client)
    assert client.post("/volume-groups/", json={"name": name}).status_code == 201
    assert client.post(f"/volume-groups/{name}/assign", json={"barcode": barcode}).status_code == 200
    dry_run = client.post(f"/cartridges/{barcode}/format/dry-run", headers=auth_headers)
    assert dry_run.status_code == 200
    token = dry_run.json()["token"]
    assert (
        client.post(
            "/cartridges/format/confirm",
            json={"barcode": barcode, "token": token},
            headers={**auth_headers, "X-Openblade-Service-Token": "openblade-controller-dev-token-do-not-expose"},
        ).status_code
        == 200
    )


def test_archive_missing_file_returns_404_or_422(client: TestClient, tmp_path: Path) -> None:
    missing_path = tmp_path / "missing"

    response = client.post(
        "/archive/",
        json={"source_path": str(missing_path), "volume_group": "photos"},
    )

    assert response.status_code in {404, 422}


def test_sharded_archive_rejects_invalid_lane_counts(client: TestClient, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "clip.txt").write_text("invalid shard count")

    empty_lanes = client.post(
        "/archive/sharded",
        json={
            "source_path": str(source),
            "volume_group": "photos",
            "lane_barcodes": [],
            "mode": "stripe",
        },
    )
    assert empty_lanes.status_code == 422

    one_lane_block_stripe = client.post(
        "/archive/sharded",
        json={
            "source_path": str(source),
            "volume_group": "photos",
            "lane_barcodes": ["VOL001L9"],
            "mode": "block_stripe",
            "block_size_mb": 1,
        },
    )
    assert one_lane_block_stripe.status_code == 422


def test_sharded_archive_rejects_unsupported_profile(client: TestClient, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "clip.txt").write_text("invalid shard profile")

    response = client.post(
        "/archive/sharded",
        json={
            "source_path": str(source),
            "volume_group": "photos",
            "lane_barcodes": ["VOL001L9"],
            "mode": "unsupported",
        },
    )

    assert response.status_code == 422


def test_concurrent_archive_jobs_preserve_catalog_and_job_state(client: TestClient, tmp_path: Path) -> None:
    _login_admin(client)
    barcodes = _data_barcodes(limit=2)
    assert len(barcodes) == 2
    _prepare_volume_group(client, "photos-a", barcodes[0])
    _prepare_volume_group(client, "photos-b", barcodes[1])

    first_source = tmp_path / "first-source"
    second_source = tmp_path / "second-source"
    first_source.mkdir()
    second_source.mkdir()
    (first_source / "a.txt").write_text("first archive")
    (second_source / "b.txt").write_text("second archive")

    other_client = TestClient(app, raise_server_exceptions=False)
    _login_admin(other_client)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(
                client.post,
                "/archive/",
                json={"source_path": str(first_source), "volume_group": "photos-a"},
            )
            second_future = pool.submit(
                other_client.post,
                "/archive/",
                json={"source_path": str(second_source), "volume_group": "photos-b"},
            )
            first_response = first_future.result()
            second_response = second_future.result()
    finally:
        other_client.close()

    assert first_response.status_code == 202
    assert second_response.status_code == 202

    catalog_response = client.get("/catalog/")
    assert catalog_response.status_code == 200
    files = {item["source_path"] for item in catalog_response.json()["files"]}
    assert files == {"/photos-a/a.txt", "/photos-b/b.txt"}

    jobs_response = client.get("/aml/jobs")
    assert jobs_response.status_code == 200
    jobs = {item["id"]: item for item in jobs_response.json()["jobList"]["job"]}
    assert jobs[first_response.json()["job_id"]]["status"] == "completed"
    assert jobs[second_response.json()["job_id"]]["status"] == "completed"


def test_archive_completion_is_visible_in_aml_jobs_and_events(client: TestClient, tmp_path: Path) -> None:
    _login_admin(client)
    barcode = _data_barcodes(limit=1)[0]
    _prepare_volume_group(client, "photos", barcode)

    source = tmp_path / "source"
    source.mkdir()
    (source / "event.txt").write_text("archive event regression")

    response = client.post(
        "/archive/",
        json={"source_path": str(source), "volume_group": "photos"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    jobs_response = client.get("/aml/jobs")
    assert jobs_response.status_code == 200
    jobs = jobs_response.json()["jobList"]["job"]
    job = next(item for item in jobs if item["id"] == job_id)
    assert job["status"] == "completed"
    assert job["type"] == "archive"

    events_response = client.get("/aml/events")
    assert events_response.status_code == 200
    events = events_response.json()["eventList"]["event"]
    assert any(
        event["component"] == "archive"
        and event["details"].get("jobId") == job_id
        and event["details"].get("volumeGroup") == "photos"
        for event in events
    )
