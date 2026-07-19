from __future__ import annotations

import hashlib
import time
from pathlib import Path, PurePosixPath

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.domain.models import MountMode
from openblade.nas.ingest import clear_ingest_state
from openblade.nas.service import NasService
from openblade.nas.types import NasFileState

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-e2e.db'}"))
    reset_context(context)
    clear_ingest_state()


def _write_source_file(path: Path, *, size: int, seed: str) -> str:
    block = hashlib.sha256(seed.encode("utf-8")).digest()
    repeats = (size // len(block)) + 1
    payload = (block * repeats)[:size]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _wait_for_ingest(job_id: str) -> dict[str, object]:
    for _ in range(60):
        status_response = client.get(f"/nas/ingest/{job_id}")
        assert status_response.status_code == 200
        payload = status_response.json()
        if payload["status"] in {"failed", "cancelled"}:
            pytest.fail(
                f"Ingest job {job_id} reached {payload['status']}: "
                f"{payload.get('errors', [])}"
            )
        if payload["status"] == "archived":
            return payload
        time.sleep(0.05)
    pytest.fail(f"Ingest job {job_id} did not reach a terminal state")


def _slot_for_barcode(barcode: str) -> int:
    inventory = get_context().library.inventory()
    for slot in inventory.slots:
        if slot.barcode is not None and str(slot.barcode) == barcode:
            return slot.slot_id
    raise AssertionError(f"Could not find slot for barcode {barcode}")


def _empty_slot(exclude: set[int]) -> int | None:
    inventory = get_context().library.inventory()
    for slot in inventory.slots:
        if slot.slot_id in exclude:
            continue
        if slot.barcode is None:
            return slot.slot_id
    return None


def _available_drive_id() -> int | None:
    inventory = get_context().library.inventory()
    for drive in inventory.drives:
        if drive.barcode is None:
            return drive.drive_id
    return None


@pytest.mark.parametrize("iteration", [1, 2, 3])
def test_nas_i3_end_to_end_archive_recall_hash_verified(iteration: int, tmp_path: Path) -> None:
    cache_root = tmp_path / f"cache-{iteration}"
    cache_root.mkdir(parents=True, exist_ok=True)
    source_root = cache_root / "source"

    source_hashes = {
        "project/docs/readme.txt": _write_source_file(
            source_root / "project/docs/readme.txt", size=256, seed=f"readme-{iteration}"
        ),
        "project/media/video.bin": _write_source_file(
            source_root / "project/media/video.bin", size=2 * 1024 * 1024, seed=f"video-{iteration}"
        ),
        "project/logs/ops.log": _write_source_file(
            source_root / "project/logs/ops.log", size=4096, seed=f"ops-{iteration}"
        ),
    }
    relative_paths = list(source_hashes.keys())
    file_sizes = {path: (source_root / path).stat().st_size for path in relative_paths}

    policy_response = client.post(
        "/nas/policies",
        json={
            "id": "archival-sequential",
            "name": "Archival Sequential",
            "policy_type": "critical_sequential",
            "default_ingest_mode": "cache_drive",
            "copies_required": 1,
            "verify_before_archive": True,
            "verify_after_archive": True,
            "allow_sharding": False,
            "max_parallelism": 1,
            "auto_clean_before_archive": True,
        },
    )
    assert policy_response.status_code == 201

    fast_policy_response = client.post(
        "/nas/policies",
        json={
            "id": "fast-recall",
            "name": "Fast Recall",
            "policy_type": "balanced",
            "default_ingest_mode": "source_stream",
            "copies_required": 1,
            "verify_before_archive": True,
            "verify_after_archive": True,
            "allow_sharding": True,
            "shard_size_bytes": 512 * 1024,
            "max_parallelism": 2,
        },
    )
    assert fast_policy_response.status_code == 201

    for pool_payload in (
        {
            "id": "archive-pool",
            "name": "Archive Pool",
            "default_policy_id": "archival-sequential",
            "replication_factor": 1,
            "backup_order_mode": "sequential",
            "access_mode": "read_write",
        },
        {
            "id": "recall-pool",
            "name": "Fast Recall Pool",
            "default_policy_id": "fast-recall",
            "replication_factor": 1,
            "backup_order_mode": "parallel",
            "access_mode": "read_write",
        },
    ):
        response = client.post("/nas/pools", json=pool_payload)
        assert response.status_code == 201

    share_response = client.post(
        "/nas/shares",
        json={
            "path": "/openblade/project-data",
            "name": "Project Data",
            "share_type": "pool",
            "default_policy_id": "archival-sequential",
            "pool_ids": ["archive-pool", "recall-pool"],
            "folder_mappings": [
                {
                    "folder_path": "/project/archive",
                    "pool_id": "archive-pool",
                    "access_mode": "read_write",
                },
                {
                    "folder_path": "/project/fast-recall",
                    "pool_id": "recall-pool",
                    "access_mode": "read_write",
                },
            ],
            "writable": True,
            "description": "Archive + fast recall mapping",
        },
    )
    assert share_response.status_code == 201
    assert set(share_response.json()["pool_ids"]) == {"archive-pool", "recall-pool"}

    cache_response = client.post(
        "/nas/cache-drives",
        json={
            "id": "cache-e2e",
            "name": "Cache E2E",
            "root_path": str(cache_root),
            "max_bytes": 8 * 1024 * 1024,
            "min_free_bytes": 256 * 1024,
            "support_reflink_or_hardlink": True,
        },
    )
    assert cache_response.status_code == 201

    tape_candidates = sorted(
        {
            str(slot.barcode)
            for slot in get_context().library.inventory().slots
            if slot.barcode is not None and not str(slot.barcode).upper().startswith("CLN")
        }
    )
    assert len(tape_candidates) >= 2

    plan_response = client.post(
        "/nas/archive-plan",
        json={
            "policy_id": "archival-sequential",
            "source_path": str(source_root),
            "pool": "archive-pool",
            "files": relative_paths,
            "file_sizes": file_sizes,
            "available_tapes": tape_candidates[:2],
            "copies": 1,
            "verify_before_archive": True,
            "verify_after_archive": True,
            "max_parallelism": 1,
        },
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["is_safe_to_enqueue"] is True
    assert plan_payload["tape_assignments"]

    ingest_response = client.post(
        "/nas/ingest/start",
        json={
            "plan_id": plan_payload["plan_id"],
            "dataset_name": f"e2e-dataset-{iteration}",
            "pool_id": "archive-pool",
            "cache_drive_id": "cache-e2e",
            "auto_clean_drives": True,
        },
    )
    assert ingest_response.status_code == 200
    ingest_payload = ingest_response.json()
    ingest_status = _wait_for_ingest(ingest_payload["job_id"])
    assert ingest_status["status"] == "archived"
    assert ingest_status["files_processed"] == len(relative_paths)
    assert ingest_status["files_failed"] == 0

    dataset_id = ingest_payload["dataset_id"]
    dataset_response = client.get(f"/nas/datasets/{dataset_id}")
    assert dataset_response.status_code == 200
    dataset_payload = dataset_response.json()
    assert dataset_payload["pool_id"] == "archive-pool"
    assert dataset_payload["status"] == "archived"
    assert dataset_payload["file_count"] == len(relative_paths)

    files_response = client.get(f"/nas/datasets/{dataset_id}/files")
    assert files_response.status_code == 200
    records = files_response.json()
    assert len(records) == len(relative_paths)
    for record in records:
        assert record["checksum_sha256"] == source_hashes[record["relative_path"]]
        assert record["tape_barcode"]

    verify_response = client.post(f"/nas/datasets/{dataset_id}/verify")
    assert verify_response.status_code == 200
    verify_payload = verify_response.json()
    assert verify_payload["files_corrupt"] == 0
    assert verify_payload["files_verified"] == len(relative_paths)
    for relative_path, checksum in source_hashes.items():
        assert verify_payload["checksums"][relative_path] == checksum

    context = get_context()
    library = context.library
    ltfs = context.ltfs
    dataset_name = dataset_payload["name"]
    records_by_tape: dict[str, list[dict[str, object]]] = {}
    for record in records:
        records_by_tape.setdefault(str(record["tape_barcode"]), []).append(record)

    for barcode, tape_records in sorted(records_by_tape.items()):
        source_slot = _slot_for_barcode(barcode)
        temporary_slot = _empty_slot({source_slot})
        assert temporary_slot is not None
        moved_out = library.move(source_slot, temporary_slot)
        assert moved_out.success
        moved_back = library.move(temporary_slot, source_slot)
        assert moved_back.success

        drive_id = _available_drive_id()
        assert drive_id is not None
        load_result = library.load(source_slot, drive_id)
        assert load_result.success
        handle = None
        try:
            handle = ltfs.mount(barcode, MountMode.READ_ONLY)
            for record in tape_records:
                tape_path = PurePosixPath("/") / dataset_name / str(record["relative_path"])
                readback_path = (
                    tmp_path
                    / f"readback-{iteration}"
                    / f"{barcode}-{str(record['relative_path']).replace('/', '_')}"
                )
                read_result = ltfs.read_file(handle, tape_path, readback_path)
                assert read_result.success
                tape_bytes = readback_path.read_bytes()
                assert hashlib.sha256(tape_bytes).hexdigest() == source_hashes[str(record["relative_path"])]
                stat = ltfs.stat(handle, tape_path)
                assert stat.checksum_sha256 == source_hashes[str(record["relative_path"])]
                assert stat.size_bytes == int(record["size_bytes"])
        finally:
            if handle is not None:
                ltfs.unmount(handle)
            unload_result = library.unload(drive_id, source_slot)
            assert unload_result.success

    service = NasService(context.catalog)
    for record in service.list_file_records(dataset_id):
        service.upsert_file_record(
            record.model_copy(update={"status": NasFileState.OFFLINE_ON_TAPE, "cache_path": None})
        )
    for record in service.list_file_records(dataset_id):
        assert record.status is NasFileState.OFFLINE_ON_TAPE
        assert record.cache_path is None

    restore_request = client.post(
        "/nas/pools/archive-pool/request-restore",
        json={
            "paths": relative_paths,
            "destination": "/openblade/fast-recall",
            "priority": 7,
            "allow_parallel": True,
            "max_drives": 2,
        },
    )
    assert restore_request.status_code == 201
    restore_payload = restore_request.json()
    assert restore_payload["unavailable_files"] == []
    assert restore_payload["missing_tapes"] == []
    restore_job_id = restore_payload["id"]

    run_restore = client.post(f"/nas/restore-jobs/{restore_job_id}/run")
    assert run_restore.status_code == 202
    run_payload = run_restore.json()
    assert run_payload["status"] == "completed"
    assert run_payload["files_restored"] == len(relative_paths)
    assert run_payload["files_failed"] == 0
    assert run_payload["bytes_restored"] > 0

    restore_status = client.get(f"/nas/restore-jobs/{restore_job_id}")
    assert restore_status.status_code == 200
    assert restore_status.json()["status"] == "completed"

    post_restore_files = client.get(f"/nas/datasets/{dataset_id}/files")
    assert post_restore_files.status_code == 200
    for record in post_restore_files.json():
        assert record["status"] == "online_cached"
        assert str(record["cache_path"]).startswith("/openblade/fast-recall")

    second_verify = client.post(f"/nas/datasets/{dataset_id}/verify")
    assert second_verify.status_code == 200
    second_verify_payload = second_verify.json()
    assert second_verify_payload["files_corrupt"] == 0
    assert second_verify_payload["files_verified"] == len(relative_paths)
    for relative_path, checksum in source_hashes.items():
        assert second_verify_payload["checksums"][relative_path] == checksum
