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
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-pool-api.db'}"))
    reset_context(context)



def nas_service() -> NasService:
    return NasService(get_context().catalog)



def seed_pool_file(pool_id: str, relative_path: str, *, dataset_id: str = "dataset-1") -> None:
    service = nas_service()
    service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))
    service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            size_bytes=64,
            checksum_sha256="abc123",
            tape_barcode="VOL001L9",
            status=NasFileState.OFFLINE_ON_TAPE,
        )
    )



def test_post_pools_returns_201() -> None:
    response = client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"})

    assert response.status_code == 201
    assert response.json()["id"] == "pool-1"



def test_get_pools_returns_created_pool() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201

    response = client.get("/nas/pools")

    assert response.status_code == 200
    assert any(pool["id"] == "pool-1" for pool in response.json())



def test_get_pool_by_id_returns_200() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201

    response = client.get("/nas/pools/pool-1")

    assert response.status_code == 200
    assert response.json()["name"] == "Pool One"



def test_browse_empty_pool_returns_200() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201

    response = client.get("/nas/pools/pool-1/browse")

    assert response.status_code == 200
    assert response.json()["entries"] == []



def test_browse_pool_with_path_returns_200() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    seed_pool_file("pool-1", "photos/a.jpg")
    seed_pool_file("pool-1", "photos/b.jpg", dataset_id="dataset-2")

    response = client.get("/nas/pools/pool-1/browse", params={"path": "photos"})

    assert response.status_code == 200
    assert [entry["name"] for entry in response.json()["entries"]] == ["a.jpg", "b.jpg"]



def test_get_pool_file_missing_returns_404() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201

    response = client.get("/nas/pools/pool-1/files/missing.txt")

    assert response.status_code == 404
    assert response.json()["detail"] == "File not found"



def test_request_restore_returns_201_with_job_id() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201

    response = client.post(
        "/nas/pools/pool-1/request-restore",
        json={"paths": ["photos/a.jpg"], "destination": "/restore-target", "priority": 7},
    )

    assert response.status_code == 201
    assert response.json()["pool_id"] == "pool-1"
    assert response.json()["paths"] == ["photos/a.jpg"]
    assert response.json()["id"]



def test_list_restore_jobs_returns_200() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    create_response = client.post(
        "/nas/pools/pool-1/request-restore",
        json={"paths": ["photos/a.jpg"]},
    )
    job_id = create_response.json()["id"]

    response = client.get("/nas/restore-jobs")

    assert response.status_code == 200
    assert any(job["id"] == job_id for job in response.json())



def test_get_restore_job_returns_200() -> None:
    assert client.post("/nas/pools", json={"id": "pool-1", "name": "Pool One"}).status_code == 201
    create_response = client.post(
        "/nas/pools/pool-1/request-restore",
        json={"paths": ["photos/a.jpg"]},
    )
    job_id = create_response.json()["id"]

    response = client.get(f"/nas/restore-jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["id"] == job_id
