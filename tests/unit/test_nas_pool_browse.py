from pathlib import Path

import pytest

from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState, NasPool


def make_nas_service(tmp_path: Path) -> NasService:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-pool-browse.db'}"))
    reset_context(context)
    return NasService(context.catalog)


def seed_pool(service: NasService, *, pool_id: str = "pool-1") -> NasPool:
    return service.upsert_pool(NasPool(id=pool_id, name="Pool One"))


def seed_dataset(service: NasService, *, pool_id: str = "pool-1", dataset_id: str = "dataset-1") -> NasDataset:
    return service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))


def seed_file(
    service: NasService,
    *,
    dataset_id: str,
    pool_id: str = "pool-1",
    relative_path: str,
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
    tape_barcode: str | None = "VOL001L9",
    size_bytes: int = 10,
    checksum_sha256: str | None = "abc123",
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


def test_browse_pool_with_no_files_returns_empty_entries(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)

    result = service.browse_pool(pool.id)

    assert result["pool_id"] == pool.id
    assert result["path"] == ""
    assert result["entries"] == []
    assert result["total_files"] == 0
    assert result["total_bytes"] == 0



def test_browse_pool_with_root_files_lists_them(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="alpha.txt", size_bytes=11)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="beta.txt", size_bytes=22)

    result = service.browse_pool(pool.id)

    assert [entry["name"] for entry in result["entries"]] == ["alpha.txt", "beta.txt"]
    assert all(entry["type"] == "file" for entry in result["entries"])
    assert result["total_files"] == 2
    assert result["total_bytes"] == 33



def test_browse_pool_with_subdirectory_shows_directory_entry(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/a.jpg")

    result = service.browse_pool(pool.id)

    assert result["entries"] == [
        {
            "name": "photos",
            "type": "directory",
            "size_bytes": 0,
            "mtime": None,
            "state": None,
            "tape_barcode": None,
            "checksum_sha256": None,
            "logical_path": "photos",
        }
    ]



def test_browse_pool_subdirectory_lists_nested_files(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/a.jpg", size_bytes=5)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/b.jpg", size_bytes=7)

    result = service.browse_pool(pool.id, "photos")

    assert [entry["name"] for entry in result["entries"]] == ["a.jpg", "b.jpg"]
    assert result["total_files"] == 2
    assert result["total_bytes"] == 12



def test_browse_pool_multiple_levels_only_shows_immediate_children(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/2024/a.jpg")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="photos/2025/b.jpg")
    seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path="docs/readme.txt")

    result = service.browse_pool(pool.id)

    assert [entry["logical_path"] for entry in result["entries"]] == ["docs", "photos"]
    assert all(entry["type"] == "directory" for entry in result["entries"])



def test_get_pool_file_detail_returns_full_record(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    record = seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="photos/a.jpg",
        size_bytes=42,
        checksum_sha256="deadbeef",
    )

    result = service.get_pool_file_detail(pool.id, "photos/a.jpg")

    assert result.id == record.id
    assert result.relative_path == "photos/a.jpg"
    assert result.checksum_sha256 == "deadbeef"
    assert result.size_bytes == 42



def test_get_pool_file_detail_missing_file_raises_key_error(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    seed_dataset(service, pool_id=pool.id)

    with pytest.raises(KeyError, match="file not found"):
        service.get_pool_file_detail(pool.id, "missing.txt")



def test_get_pool_file_detail_missing_pool_raises_key_error(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(KeyError, match="pool not found"):
        service.get_pool_file_detail("missing-pool", "missing.txt")


@pytest.mark.parametrize(
    ("status", "tape_barcode", "expected"),
    [
        (NasFileState.HYDRATING, "VOL001L9", NasFileState.HYDRATING),
        (NasFileState.FAILED, "VOL001L9", NasFileState.FAILED),
        (NasFileState.CORRUPT, "VOL001L9", NasFileState.CORRUPT),
        (NasFileState.EXPORTED, "VOL001L9", NasFileState.EXPORTED),
        (NasFileState.OFFLINE_ON_TAPE, None, NasFileState.MISSING_TAPE),
        (NasFileState.OFFLINE_ON_TAPE, "VOL001L9", NasFileState.OFFLINE_ON_TAPE),
    ],
)
def test_derive_file_state_respects_record_state(
    tmp_path: Path,
    status: NasFileState,
    tape_barcode: str | None,
    expected: NasFileState,
) -> None:
    service = make_nas_service(tmp_path)
    record = NasFileRecord(dataset_id="dataset-1", relative_path="a.txt", status=status, tape_barcode=tape_barcode)

    assert service.derive_file_state(record) is expected



def test_derive_file_state_marks_loaded_tape_as_online_cached(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    record = NasFileRecord(
        dataset_id="dataset-1",
        relative_path="a.txt",
        status=NasFileState.OFFLINE_ON_TAPE,
        tape_barcode="VOL001L9",
    )

    assert service.derive_file_state(record, loaded_tapes=["VOL001L9"]) is NasFileState.ONLINE_CACHED



def test_derive_file_state_marks_unloaded_tape_as_offline(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    record = NasFileRecord(
        dataset_id="dataset-1",
        relative_path="a.txt",
        status=NasFileState.ONLINE_CACHED,
        tape_barcode="VOL001L9",
    )

    assert service.derive_file_state(record, loaded_tapes=["VOL002L9"]) is NasFileState.OFFLINE_ON_TAPE



def test_browse_pool_counts_online_offline_and_hydrating_files(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool_id=pool.id)
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="online.txt",
        status=NasFileState.ONLINE_CACHED,
        size_bytes=10,
    )
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="offline.txt",
        status=NasFileState.OFFLINE_ON_TAPE,
        size_bytes=20,
    )
    seed_file(
        service,
        dataset_id=dataset.id,
        pool_id=pool.id,
        relative_path="hydrating.txt",
        status=NasFileState.HYDRATING,
        size_bytes=30,
    )

    result = service.browse_pool(pool.id)

    assert result["online_count"] == 1
    assert result["offline_count"] == 1
    assert result["hydrating_count"] == 1
    assert result["total_files"] == 3
    assert result["total_bytes"] == 60



def test_browse_pool_missing_pool_raises_key_error(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)

    with pytest.raises(KeyError, match="pool not found"):
        service.browse_pool("missing-pool")
