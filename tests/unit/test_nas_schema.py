from pathlib import Path

import pytest
from pydantic import ValidationError

from openblade.bootstrap import create_context
from openblade.config import OpenBladeConfig
from openblade.nas.service import NasService
from openblade.nas.types import (
    DatasetStatus,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
    NasRestoreJob,
    RestoreJobStatus,
)


def make_nas_service(tmp_path: Path) -> NasService:
    context = create_context(OpenBladeConfig(db_url=f"sqlite+aiosqlite:///{tmp_path / 'openblade.db'}"))
    return NasService(context.catalog)


def test_pool_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = NasPool(name="photo-archive", description="Photo pool", volume_group_ids=["vg-1"])

    saved = service.upsert_pool(pool)
    fetched = service.get_pool(pool.id)

    assert fetched is not None
    assert saved.id == fetched.id == pool.id
    assert fetched.name == pool.name
    assert fetched.description == pool.description
    assert fetched.created_at is not None
    assert fetched.updated_at is not None

    updated = service.upsert_pool(pool.model_copy(update={"description": "Updated pool"}))
    assert updated.description == "Updated pool"
    assert updated in service.list_pools()
    assert service.delete_pool(pool.id) is True
    assert service.get_pool(pool.id) is None


def test_dataset_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    pool = service.upsert_pool(NasPool(name="dataset-pool"))
    dataset = NasDataset(
        pool_id=pool.id,
        name="dataset-a",
        source_path="/srv/source-a",
        tape_set=["TAPE001", "TAPE002"],
    )

    saved = service.upsert_dataset(dataset)
    fetched = service.get_dataset(dataset.id)

    assert fetched is not None
    assert saved.id == fetched.id == dataset.id
    assert fetched.pool_id == pool.id
    assert fetched.tape_set == ["TAPE001", "TAPE002"]
    assert [item.id for item in service.list_datasets(pool.id)] == [dataset.id]

    updated = service.upsert_dataset(
        dataset.model_copy(update={"status": DatasetStatus.ARCHIVED, "file_count": 4})
    )
    assert updated.status == "archived"
    assert updated.file_count == 4
    assert service.delete_dataset(dataset.id) is True
    assert service.get_dataset(dataset.id) is None


def test_file_record_status_update(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    dataset = service.upsert_dataset(NasDataset(name="files"))
    file_record = service.upsert_file_record(
        NasFileRecord(dataset_id=dataset.id, relative_path="photos/a.jpg")
    )

    assert service.update_file_status(file_record.id, NasFileState.HYDRATING.value) is True

    fetched = service.get_file_record(file_record.id)
    assert fetched is not None
    assert fetched.status is NasFileState.HYDRATING
    assert service.list_file_records(dataset.id) == [fetched]


def test_restore_job_crud_round_trip(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    job = NasRestoreJob(paths=["/openblade/pools/photo-archive/photos/a.jpg"])

    saved = service.upsert_restore_job(job)
    fetched = service.get_restore_job(job.id)

    assert fetched is not None
    assert saved.id == fetched.id == job.id
    assert fetched.paths == job.paths
    assert fetched.status.value == "queued"
    assert service.update_restore_job_status(job.id, "running", files_restored=1) is True

    updated = service.get_restore_job(job.id)
    assert updated is not None
    assert updated.status.value == "running"
    assert updated.files_restored == 1
    assert service.delete_restore_job(job.id) is True
    assert service.get_restore_job(job.id) is None


def test_restore_job_status_enum_completeness() -> None:
    assert {status.value for status in RestoreJobStatus} == {
        "queued",
        "planning",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
    }


def test_file_state_enum_completeness() -> None:
    assert {state.value for state in NasFileState} == {
        "online_cached",
        "offline_on_tape",
        "hydrating",
        "missing_tape",
        "failed",
        "corrupt",
        "exported",
    }


def test_dataset_status_enum() -> None:
    dataset = NasDataset(name="dataset-a", status=DatasetStatus.ARCHIVING)

    assert dataset.status is DatasetStatus.ARCHIVING
    assert DatasetStatus.ARCHIVED.value == "archived"
    assert DatasetStatus.FAILED.value == "failed"
    assert DatasetStatus.VERIFIED.value == "verified"
    assert DatasetStatus.EXPORTED.value == "exported"
    assert DatasetStatus.CANCELLED.value == "cancelled"


def test_pool_validates_name() -> None:
    with pytest.raises(ValidationError):
        NasPool(name="")


def test_pool_name_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        NasPool(name="   ")


def test_dataset_name_rejects_whitespace_only() -> None:
    with pytest.raises(ValidationError):
        NasDataset(name="   ")


@pytest.mark.parametrize("priority", [0, 11])
def test_restore_job_priority_bounds(priority: int) -> None:
    with pytest.raises(ValidationError):
        NasRestoreJob(priority=priority)


@pytest.mark.parametrize("max_drives", [0, 9])
def test_restore_job_max_drives_bounds(max_drives: int) -> None:
    with pytest.raises(ValidationError):
        NasRestoreJob(max_drives=max_drives)


def test_restore_job_status_update_is_status_only(tmp_path: Path) -> None:
    service = make_nas_service(tmp_path)
    job = service.upsert_restore_job(
        NasRestoreJob(
            paths=["/openblade/pools/photo-archive/photos/a.jpg"],
            bytes_restored=10,
            files_restored=2,
            files_failed=1,
            error_message="existing",
        )
    )

    before = service.get_restore_job(job.id)
    assert before is not None

    assert service.update_restore_job_status(job.id, "running") is True

    after = service.get_restore_job(job.id)
    assert after is not None
    assert after.status is RestoreJobStatus.RUNNING
    assert after.updated_at is not None
    assert after.updated_at != before.updated_at
    assert after.bytes_restored == before.bytes_restored
    assert after.files_restored == before.files_restored
    assert after.files_failed == before.files_failed
    assert after.error_message == before.error_message
    assert after.completed_at == before.completed_at
