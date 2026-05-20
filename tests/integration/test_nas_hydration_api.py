from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState, RestoreJobStatus

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-hydration-api.db'}"))
    reset_context(context)


def nas_service() -> NasService:
    return NasService(get_context().catalog)


def seed_pool_file(
    pool_id: str,
    relative_path: str,
    *,
    dataset_id: str = "dataset-1",
    tape_barcode: str = "VOL001L9",
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
) -> None:
    service = nas_service()
    service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))
    service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            size_bytes=64,
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


def test_post_run_executes_restore_job() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])

    response = client.post(f"/nas/restore-jobs/{job['id']}/run")

    assert response.status_code == 202
    assert response.json()["status"] == RestoreJobStatus.COMPLETED.value


def test_post_cancel_sets_cancelled_status() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])

    response = client.post(f"/nas/restore-jobs/{job['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == RestoreJobStatus.CANCELLED.value


def test_post_pause_sets_paused_status() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])
    nas_service().update_restore_job_status(job["id"], RestoreJobStatus.RUNNING.value)

    response = client.post(f"/nas/restore-jobs/{job['id']}/pause")

    assert response.status_code == 200
    assert response.json()["status"] == RestoreJobStatus.PAUSED.value


def test_post_resume_runs_paused_job_to_completion() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])
    nas_service().update_restore_job_status(job["id"], RestoreJobStatus.PAUSED.value)

    response = client.post(f"/nas/restore-jobs/{job['id']}/resume")

    assert response.status_code == 200
    assert response.json()["status"] == RestoreJobStatus.COMPLETED.value


def test_post_retry_reruns_failed_job() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])
    service = nas_service()
    record = service.list_pool_file_records("pool-1")[0]
    service.upsert_file_record(record.model_copy(update={"status": NasFileState.FAILED}))
    service.update_restore_job_status(job["id"], RestoreJobStatus.FAILED.value)

    response = client.post(f"/nas/restore-jobs/{job['id']}/retry")

    assert response.status_code == 200
    assert response.json()["status"] == RestoreJobStatus.COMPLETED.value
    assert response.json()["files_restored"] == 1


def test_post_run_on_non_queued_job_returns_400() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])
    nas_service().update_restore_job_status(job["id"], RestoreJobStatus.RUNNING.value)

    response = client.post(f"/nas/restore-jobs/{job['id']}/run")

    assert response.status_code == 400


def test_get_restore_job_after_run_reports_bytes_restored() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    job = create_restore_job("pool-1", ["photos/a.jpg"])
    assert client.post(f"/nas/restore-jobs/{job['id']}/run").status_code == 202

    response = client.get(f"/nas/restore-jobs/{job['id']}")

    assert response.status_code == 200
    assert response.json()["bytes_restored"] > 0


def test_post_run_on_missing_job_returns_404() -> None:
    response = client.post("/nas/restore-jobs/missing-job/run")

    assert response.status_code == 404
