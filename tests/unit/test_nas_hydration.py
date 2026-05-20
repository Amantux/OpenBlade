from __future__ import annotations

import threading

import pytest

from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.hydration import HydrationExecutor
from openblade.nas.restore_planner import RestorePlanner
from openblade.nas.service import NasService
from openblade.nas.types import NasDataset, NasFileRecord, NasFileState, NasPool, NasRestoreJob, RestoreJobStatus, RestorePlanRequest


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-hydration.db'}"))
    reset_context(context)


def nas_service() -> NasService:
    return NasService(get_context().catalog)


def hydration_executor(service: NasService | None = None) -> HydrationExecutor:
    return HydrationExecutor(service or nas_service(), get_context().ltfs)


def seed_pool(service: NasService, pool_id: str = "pool-1") -> NasPool:
    return service.upsert_pool(NasPool(id=pool_id, name=f"Pool {pool_id}"))


def seed_dataset(service: NasService, pool_id: str, dataset_id: str = "dataset-1") -> NasDataset:
    return service.upsert_dataset(NasDataset(id=dataset_id, pool_id=pool_id, name=f"dataset-{dataset_id}"))


def seed_file(
    service: NasService,
    *,
    dataset_id: str,
    pool_id: str,
    relative_path: str,
    tape_barcode: str,
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE,
) -> NasFileRecord:
    return service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset_id,
            pool_id=pool_id,
            relative_path=relative_path,
            tape_barcode=tape_barcode,
            size_bytes=64,
            checksum_sha256="seeded",
            status=status,
        )
    )


def create_restore_job(
    service: NasService,
    *,
    pool_id: str,
    paths: list[str] | None = None,
    allow_parallel: bool = True,
    max_drives: int = 2,
    status: RestoreJobStatus = RestoreJobStatus.QUEUED,
) -> NasRestoreJob:
    request = RestorePlanRequest(
        pool_id=pool_id,
        paths=paths or [],
        allow_parallel=allow_parallel,
        max_drives=max_drives,
    )
    plan = RestorePlanner(service).plan(request)
    return service.upsert_restore_job(
        NasRestoreJob(
            pool_id=pool_id,
            paths=paths or [],
            allow_parallel=allow_parallel,
            max_drives=max_drives,
            status=status,
            required_tapes=plan.required_tapes,
            missing_tapes=plan.missing_tapes,
            exported_tapes=plan.exported_tapes,
            tape_load_order=plan.tape_load_order,
            parallel_restore_groups={
                f"group-{index + 1}": group for index, group in enumerate(plan.parallel_restore_groups)
            },
            estimated_bytes=plan.estimated_bytes,
            unavailable_files=plan.unavailable_files,
            warnings=plan.warnings,
        )
    )


def setup_restore_fixture(*, paths: list[str] | None = None, tapes: list[str] | None = None) -> tuple[NasService, NasRestoreJob]:
    service = nas_service()
    pool = seed_pool(service)
    dataset = seed_dataset(service, pool.id)
    requested_paths = paths or ["photos/a.jpg"]
    barcodes = tapes or ["VOL001L9"] * len(requested_paths)
    for logical_path, barcode in zip(requested_paths, barcodes, strict=False):
        seed_file(service, dataset_id=dataset.id, pool_id=pool.id, relative_path=logical_path, tape_barcode=barcode)
    return service, create_restore_job(service, pool_id=pool.id, paths=requested_paths)


def test_run_single_file_marks_record_online_and_job_completed() -> None:
    service, job = setup_restore_fixture()

    restored_job = hydration_executor(service).run(job.id)

    record = service.list_pool_file_records(job.pool_id)[0]
    assert restored_job.status is RestoreJobStatus.COMPLETED
    assert record.status is NasFileState.ONLINE_CACHED


def test_run_multiple_files_across_multiple_tapes_restores_all() -> None:
    service, job = setup_restore_fixture(
        paths=["photos/a.jpg", "photos/b.jpg", "photos/c.jpg"],
        tapes=["VOL001L9", "VOL002L9", "VOL003L9"],
    )

    restored_job = hydration_executor(service).run(job.id)

    assert restored_job.status is RestoreJobStatus.COMPLETED
    assert restored_job.files_restored == 3
    assert all(record.status is NasFileState.ONLINE_CACHED for record in service.list_pool_file_records(job.pool_id))


def test_run_continues_after_per_file_error_and_marks_partial_success(monkeypatch) -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])
    executor = hydration_executor(service)
    original = executor._simulate_content

    def flaky(record: NasFileRecord) -> bytes:
        if record.relative_path == "photos/b.jpg":
            raise RuntimeError("boom")
        return original(record)

    monkeypatch.setattr(executor, "_simulate_content", flaky)

    restored_job = executor.run(job.id)
    records = {record.relative_path: record for record in service.list_pool_file_records(job.pool_id)}

    assert restored_job.status is RestoreJobStatus.COMPLETED
    assert restored_job.partial_success is True
    assert records["photos/a.jpg"].status is NasFileState.ONLINE_CACHED
    assert records["photos/b.jpg"].status is NasFileState.FAILED


def test_cancel_from_queued_sets_cancelled() -> None:
    service, job = setup_restore_fixture()

    cancelled = hydration_executor(service).cancel(job.id)

    assert cancelled.status is RestoreJobStatus.CANCELLED
    assert cancelled.completed_at is not None


def test_cancel_during_running_stops_processing() -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])
    executor = hydration_executor(service)
    started = threading.Event()
    release = threading.Event()
    original = executor._simulate_content

    def blocking(record: NasFileRecord) -> bytes:
        if record.relative_path == "photos/a.jpg":
            started.set()
            release.wait(timeout=5)
        return original(record)

    thread = threading.Thread(target=lambda: executor.run(job.id), daemon=True)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(executor, "_simulate_content", blocking)
    try:
        thread.start()
        assert started.wait(timeout=5)
        cancelled = hydration_executor(service).cancel(job.id)
        release.set()
        thread.join(timeout=5)
    finally:
        release.set()
        monkeypatch.undo()

    records = {record.relative_path: record for record in service.list_pool_file_records(job.pool_id)}
    assert cancelled.status is RestoreJobStatus.CANCELLED
    assert records["photos/b.jpg"].status is NasFileState.OFFLINE_ON_TAPE


def test_pause_from_running_sets_paused() -> None:
    service, job = setup_restore_fixture()
    service.update_restore_job_status(job.id, RestoreJobStatus.RUNNING.value)

    paused = hydration_executor(service).pause(job.id)

    assert paused.status is RestoreJobStatus.PAUSED


def test_resume_from_paused_completes_remaining_files() -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])
    records = service.list_pool_file_records(job.pool_id)
    service.upsert_file_record(records[0].model_copy(update={"status": NasFileState.ONLINE_CACHED}))
    service.update_restore_job_status(job.id, RestoreJobStatus.PAUSED.value, files_restored=1)

    resumed = hydration_executor(service).resume(job.id)

    assert resumed.status is RestoreJobStatus.COMPLETED
    assert resumed.files_restored == 2


def test_retry_from_failed_only_reruns_failed_files(monkeypatch) -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])
    records = {record.relative_path: record for record in service.list_pool_file_records(job.pool_id)}
    service.upsert_file_record(records["photos/a.jpg"].model_copy(update={"status": NasFileState.ONLINE_CACHED}))
    service.upsert_file_record(records["photos/b.jpg"].model_copy(update={"status": NasFileState.FAILED}))
    service.update_restore_job_status(job.id, RestoreJobStatus.FAILED.value)
    seen: list[str] = []
    executor = hydration_executor(service)
    original = executor._simulate_content

    def tracked(record: NasFileRecord) -> bytes:
        seen.append(record.relative_path)
        return original(record)

    monkeypatch.setattr(executor, "_simulate_content", tracked)

    retried = executor.retry(job.id)

    assert retried.status is RestoreJobStatus.COMPLETED
    assert seen == ["photos/b.jpg"]


def test_run_from_non_queued_raises_value_error() -> None:
    service, job = setup_restore_fixture()
    service.update_restore_job_status(job.id, RestoreJobStatus.RUNNING.value)

    with pytest.raises(ValueError):
        hydration_executor(service).run(job.id)


def test_pause_from_non_running_raises_value_error() -> None:
    service, job = setup_restore_fixture()

    with pytest.raises(ValueError):
        hydration_executor(service).pause(job.id)


def test_bytes_restored_accumulates_simulated_payload_sizes() -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])

    restored = hydration_executor(service).run(job.id)

    expected = sum(
        len(f"HYDRATED:{record.relative_path}:{record.tape_barcode}".encode())
        for record in service.list_pool_file_records(job.pool_id)
    )
    assert restored.bytes_restored == expected


def test_file_counters_track_success_and_failure(monkeypatch) -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])
    executor = hydration_executor(service)
    original = executor._simulate_content

    def flaky(record: NasFileRecord) -> bytes:
        if record.relative_path == "photos/b.jpg":
            raise RuntimeError("fail second")
        return original(record)

    monkeypatch.setattr(executor, "_simulate_content", flaky)

    restored = executor.run(job.id)

    assert restored.files_restored == 1
    assert restored.files_failed == 1


def test_file_record_status_updates_to_online_cached_after_restore() -> None:
    service, job = setup_restore_fixture()

    hydration_executor(service).run(job.id)

    record = service.list_pool_file_records(job.pool_id)[0]
    assert record.status is NasFileState.ONLINE_CACHED


def test_partial_success_is_false_when_all_files_succeed() -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg"], tapes=["VOL001L9", "VOL002L9"])

    restored = hydration_executor(service).run(job.id)

    assert restored.status is RestoreJobStatus.COMPLETED
    assert restored.partial_success is False


def test_parallel_restore_groups_are_processed_sequentially() -> None:
    service, job = setup_restore_fixture(paths=["photos/a.jpg", "photos/b.jpg", "photos/c.jpg"], tapes=["VOL001L9", "VOL002L9", "VOL003L9"])
    seen: list[str] = []
    executor = hydration_executor(service)
    original = executor._simulate_content

    def tracked(record: NasFileRecord) -> bytes:
        seen.append(record.tape_barcode)
        return original(record)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(executor, "_simulate_content", tracked)
    try:
        executor.run(job.id)
    finally:
        monkeypatch.undo()

    assert seen == ["VOL001L9", "VOL002L9", "VOL003L9"]
