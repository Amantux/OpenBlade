import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import (
    DatasetStatus,
    IngestMode,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
    PolicyType,
    StoragePolicy,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-dataset-api.db'}"))
    reset_context(context)


def nas_service() -> NasService:
    return NasService(get_context().catalog)


def seed_dataset_bundle(*, dataset_id: str = "dataset-1", pool_id: str = "pool-1") -> str:
    service = nas_service()
    service.upsert_pool(NasPool(id=pool_id, name="Pool One"))
    service.upsert_policy(StoragePolicy(id="policy-1", name="Balanced Policy", policy_type=PolicyType.BALANCED))
    dataset = service.upsert_dataset(
        NasDataset(
            id=dataset_id,
            pool_id=pool_id,
            name="dataset-one",
            policy_id="policy-1",
            ingest_mode=IngestMode.CACHE_DRIVE,
        )
    )
    service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset.id,
            pool_id=pool_id,
            relative_path="docs/a.txt",
            size_bytes=64,
            tape_barcode="VOL001L9",
            status=NasFileState.OFFLINE_ON_TAPE,
        )
    )
    service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset.id,
            pool_id=pool_id,
            relative_path="docs/b.txt",
            size_bytes=32,
            checksum_sha256="preset",
            tape_barcode="VOL002L9",
            status=NasFileState.OFFLINE_ON_TAPE,
        )
    )
    return dataset.id


def test_get_datasets_returns_200_list() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.get("/nas/datasets")

    assert response.status_code == 200
    assert any(item["id"] == dataset_id for item in response.json())


def test_get_dataset_by_id_returns_tape_set_and_shard_map() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.get(f"/nas/datasets/{dataset_id}")

    assert response.status_code == 200
    assert response.json()["tape_set"] == ["VOL001L9", "VOL002L9"]
    assert response.json()["shard_map"] == {
        "VOL001L9": ["docs/a.txt"],
        "VOL002L9": ["docs/b.txt"],
    }


def test_get_dataset_files_returns_200_list() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.get(f"/nas/datasets/{dataset_id}/files", params={"skip": 0, "limit": 10})

    assert response.status_code == 200
    assert [item["relative_path"] for item in response.json()] == ["docs/a.txt", "docs/b.txt"]


def test_get_dataset_manifest_returns_200_with_shard_map() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.get(f"/nas/datasets/{dataset_id}/manifest")

    assert response.status_code == 200
    assert response.json()["shard_map"] == {
        "VOL001L9": ["docs/a.txt"],
        "VOL002L9": ["docs/b.txt"],
    }
    assert response.json()["generated_at"].endswith("Z")


def test_post_verify_returns_200_with_checksums() -> None:
    dataset_id = seed_dataset_bundle()
    expected = hashlib.sha256(b"docs/a.txt:VOL001L9").hexdigest()

    response = client.post(f"/nas/datasets/{dataset_id}/verify")

    assert response.status_code == 200
    assert response.json()["files_verified"] == 2
    assert response.json()["checksums"]["docs/a.txt"] == expected


def test_post_export_returns_200_and_exported_status() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.post(f"/nas/datasets/{dataset_id}/export")

    assert response.status_code == 200
    assert response.json()["status"] == DatasetStatus.EXPORTED.value
    files_response = client.get(f"/nas/datasets/{dataset_id}/files")
    assert all(item["status"] == NasFileState.EXPORTED.value for item in files_response.json())


def test_get_dataset_report_returns_200_with_metadata() -> None:
    dataset_id = seed_dataset_bundle()

    response = client.get(f"/nas/datasets/{dataset_id}/report")

    assert response.status_code == 200
    assert response.json()["dataset"]["id"] == dataset_id
    assert response.json()["checksums"]["docs/b.txt"] == "preset"


def test_post_fuse_open_returns_200_with_action() -> None:
    dataset_id = seed_dataset_bundle()
    assert dataset_id

    response = client.post("/nas/fuse/open", json={"pool_id": "pool-1", "logical_path": "docs/a.txt"})

    assert response.status_code == 200
    assert response.json()["action"] == "queue_hydration"
