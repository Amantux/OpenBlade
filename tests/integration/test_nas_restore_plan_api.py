from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-restore-plan-api.db'}"))
    reset_context(context)


def nas_service() -> NasService:
    return NasService(get_context().catalog)


def seed_pool_file(
    pool_id: str,
    relative_path: str,
    *,
    dataset_id: str = "dataset-1",
    tape_barcode: str | None = "VOL001L9",
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
    size_bytes: int = 64,
) -> None:
    service = nas_service()
    service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))
    service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            size_bytes=size_bytes,
            checksum_sha256="abc123",
            tape_barcode=tape_barcode,
            status=status,
        )
    )


def create_restore_job(pool_id: str, paths: list[str] | None = None) -> dict[str, object]:
    response = client.post(
        f"/nas/pools/{pool_id}/request-restore",
        json={"paths": paths or [], "destination": "/restore-target", "priority": 7},
    )
    assert response.status_code == 201
    return response.json()


def test_post_restore_plan_returns_restore_plan_fields() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg", tape_barcode="VOL001L9")

    response = client.post("/nas/restore-plan", json={"pool_id": "pool-1", "paths": ["photos/a.jpg"]})

    body = response.json()

    assert response.status_code == 200
    assert body["pool_id"] == "pool-1"
    assert body["required_tapes"] == ["VOL001L9"]
    assert body["batches_by_tape"] == {"VOL001L9": ["photos/a.jpg"]}


def test_post_restore_plan_filters_requested_files() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg", tape_barcode="VOL001L9")
    seed_pool_file("pool-1", "photos/b.jpg", tape_barcode="VOL002L9", dataset_id="dataset-2")

    response = client.post("/nas/restore-plan", json={"pool_id": "pool-1", "paths": ["photos/b.jpg"]})

    assert response.status_code == 200
    assert response.json()["required_tapes"] == ["VOL002L9"]
    assert response.json()["batches_by_tape"] == {"VOL002L9": ["photos/b.jpg"]}


def test_post_restore_plan_missing_pool_returns_404() -> None:
    response = client.post("/nas/restore-plan", json={"pool_id": "missing-pool", "paths": ["photos/a.jpg"]})

    assert response.status_code == 404
    assert response.json()["detail"] == "Pool missing-pool not found"


def test_request_restore_returns_required_tapes_from_planner() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg", tape_barcode="VOL009L9")

    response = client.post(
        "/nas/pools/pool-1/request-restore",
        json={"paths": ["photos/a.jpg"], "destination": "/restore-target", "priority": 7},
    )

    assert response.status_code == 201
    assert response.json()["required_tapes"] == ["VOL009L9"]


def test_list_restore_jobs_includes_created_job() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg", tape_barcode="VOL001L9")
    job = create_restore_job("pool-1", ["photos/a.jpg"])

    response = client.get("/nas/restore-jobs")

    assert response.status_code == 200
    assert any(item["id"] == job["id"] and item["required_tapes"] == ["VOL001L9"] for item in response.json())


def test_get_restore_job_returns_full_job_with_required_tapes() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg", tape_barcode="VOL001L9")
    job = create_restore_job("pool-1", ["photos/a.jpg"])

    response = client.get(f"/nas/restore-jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == job["id"]
    assert response.json()["required_tapes"] == ["VOL001L9"]
    assert response.json()["paths"] == ["photos/a.jpg"]
