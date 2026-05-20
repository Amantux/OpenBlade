import asyncio
import hashlib
from pathlib import Path

from openblade.api.nas_config import export_dataset, get_dataset_manifest, get_dataset_report, verify_dataset
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.fuse_hook import FuseHook
from openblade.nas.service import NasService
from openblade.nas.types import DatasetStatus, IngestMode, NasDataset, NasFileRecord, NasFileState, NasPool, StoragePolicy
from openblade.nas.types import PolicyType


def make_nas_service(tmp_path: Path) -> NasService:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-dataset-detail.db'}"))
    reset_context(context)
    return NasService(context.catalog)


def seed_pool(service: NasService, *, pool_id: str = "pool-1") -> NasPool:
    return service.upsert_pool(NasPool(id=pool_id, name="Pool One"))


def seed_policy(service: NasService, *, policy_id: str = "policy-1") -> StoragePolicy:
    return service.upsert_policy(
        StoragePolicy(id=policy_id, name="Balanced Policy", policy_type=PolicyType.BALANCED)
    )


def seed_dataset(
    service: NasService,
    *,
    dataset_id: str = "dataset-1",
    pool_id: str = "pool-1",
    policy_id: str | None = None,
) -> NasDataset:
    return service.upsert_dataset(
        NasDataset(
            id=dataset_id,
            pool_id=pool_id,
            name="dataset-one",
            ingest_mode=IngestMode.CACHE_DRIVE,
            policy_id=policy_id,
        )
    )


def seed_file(
    service: NasService,
    *,
    dataset_id: str,
    pool_id: str = "pool-1",
    relative_path: str,
    size_bytes: int = 10,
    checksum_sha256: str | None = None,
    tape_barcode: str | None = "VOL001L9",
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
) -> NasFileRecord:
    return service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            tape_barcode=tape_barcode,
            status=status,
        )
    )


def test_get_dataset_detail_tape_set_derived_from_file_records(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="a.txt", tape_barcode="VOL002L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="b.txt", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="c.txt", tape_barcode="VOL002L9")

    detail = service.get_dataset_detail(dataset.id)

    assert detail["tape_set"] == ["VOL001L9", "VOL002L9"]


def test_get_dataset_detail_shard_map_is_correct(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/b.txt", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="media/c.txt", tape_barcode="VOL002L9")

    detail = service.get_dataset_detail(dataset.id)

    assert detail["shard_map"] == {
        "VOL001L9": ["docs/a.txt", "docs/b.txt"],
        "VOL002L9": ["media/c.txt"],
    }


def test_get_dataset_detail_file_count_and_total_bytes_are_correct(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="a.bin", size_bytes=11)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="b.bin", size_bytes=22)

    detail = service.get_dataset_detail(dataset.id)

    assert detail["file_count"] == 2
    assert detail["total_bytes"] == 33


def test_get_dataset_detail_copies_completed_counts_distinct_tapes(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="a.bin", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="b.bin", tape_barcode="VOL001L9")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="c.bin", tape_barcode="VOL002L9")

    detail = service.get_dataset_detail(dataset.id)

    assert detail["copies_completed"] == 2


def test_get_dataset_detail_policy_name_is_looked_up(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    policy = seed_policy(service)
    dataset = seed_dataset(service, pool_id=pool.id, policy_id=policy.id)

    detail = service.get_dataset_detail(dataset.id)

    assert detail["policy_name"] == policy.name


def test_verify_seeds_checksums_for_files_without_checksum(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt", checksum_sha256=None)

    result = asyncio.run(verify_dataset(dataset.id, service))
    updated = service.get_file_record(record.id)
    expected = hashlib.sha256(f"docs/a.txt:{record.tape_barcode}".encode()).hexdigest()

    assert result["files_verified"] == 1
    assert result["files_corrupt"] == 0
    assert result["files_updated"] == 1
    assert result["checksums"] == {"docs/a.txt": expected}
    assert updated is not None and updated.checksum_sha256 == expected


def test_verify_marks_corrupt_file_when_checksum_mismatches(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="docs/a.txt",
        checksum_sha256="mismatch",
    )

    result = asyncio.run(verify_dataset(dataset.id, service))
    updated = service.get_file_record(record.id)

    assert result["files_corrupt"] == 1
    assert updated is not None and updated.status is NasFileState.CORRUPT


def test_export_sets_dataset_and_files_to_exported(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/b.txt")

    detail = asyncio.run(export_dataset(dataset.id, service))
    file_states = [record.status for record in service.list_file_records(dataset.id)]

    assert detail["status"] == DatasetStatus.EXPORTED.value
    assert file_states == [NasFileState.EXPORTED, NasFileState.EXPORTED]


def test_manifest_returns_expected_structure(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt", size_bytes=99)

    manifest = asyncio.run(get_dataset_manifest(dataset.id, service))

    assert manifest["dataset_id"] == dataset.id
    assert manifest["policy_id"] == dataset.policy_id
    assert manifest["ingest_mode"] == IngestMode.CACHE_DRIVE.value
    assert manifest["tape_set"] == [record.tape_barcode]
    assert manifest["shard_map"] == {record.tape_barcode: [record.relative_path]}
    assert manifest["files"] == [
        {
            "logical_path": record.relative_path,
            "size_bytes": 99,
            "checksum_sha256": None,
            "tape_barcode": record.tape_barcode,
            "state": NasFileState.OFFLINE_ON_TAPE.value,
        }
    ]
    assert manifest["total_files"] == 1
    assert manifest["total_bytes"] == 99
    assert manifest["generated_at"].endswith("Z")


def test_report_includes_dataset_metadata_and_checksums(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt", checksum_sha256="abc")

    report = asyncio.run(get_dataset_report(dataset.id, service))

    assert report["dataset"]["id"] == dataset.id
    assert report["files"][0]["logical_path"] == record.relative_path
    assert report["checksums"] == {record.relative_path: "abc"}
    assert report["generated_at"].endswith("Z")


def test_fuse_on_file_open_allows_online_cached_file(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="docs/a.txt",
        status=NasFileState.ONLINE_CACHED,
    )

    result = FuseHook(service).on_file_open(pool.id, "docs/a.txt")

    assert result == {"action": "allow", "message": None}


def test_fuse_on_file_open_queues_hydration_for_offline_file(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/a.txt")

    result = FuseHook(service).on_file_open(pool.id, "docs/a.txt")

    assert result == {
        "action": "queue_hydration",
        "message": "File is offline. Hydration queued.",
        "tape_barcode": record.tape_barcode,
    }


def test_fuse_on_file_open_waits_for_hydrating_file(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="docs/a.txt",
        status=NasFileState.HYDRATING,
    )

    result = FuseHook(service).on_file_open(pool.id, "docs/a.txt")

    assert result == {"action": "wait", "message": "File is being hydrated. Try again shortly."}


def test_fuse_on_file_open_returns_error_for_missing_file(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)

    hook = FuseHook(service)
    result = hook.on_file_open(pool.id, "missing.txt")

    assert result == {"action": "error", "message": "File not found"}
    assert hook.get_access_log()[0]["state"] == "not_found"
