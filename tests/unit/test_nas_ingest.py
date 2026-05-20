from pathlib import Path

from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, get_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.ingest import (
    cancel_ingest_job,
    clear_ingest_state,
    get_ingest_job,
    register_archive_plan,
    run_ingest_job,
    start_ingest_job,
)
from openblade.nas.service import NasService
from openblade.nas.types import (
    ArchivePlan,
    CacheDriveConfig,
    DatasetStatus,
    IngestMode,
    NasFileState,
    NasPool,
    TapeAssignment,
)

client = TestClient(app)


def _write_file(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def _setup_service(tmp_path: Path) -> tuple[NasService, Path]:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'nas-ingest.db'}"))
    reset_context(context)
    clear_ingest_state()
    service = NasService(context.catalog)
    cache_root = tmp_path / "cache"
    service.upsert_pool(NasPool(id="pool-1", name="pool-1"))
    service.upsert_cache_drive(
        CacheDriveConfig(
            id="cache-1",
            name="Cache 1",
            root_path=str(cache_root),
            max_bytes=1_000_000,
            min_free_bytes=0,
        )
    )
    return service, cache_root


def _make_plan(cache_root: Path) -> ArchivePlan:
    first = _write_file(cache_root / "dataset" / "a.txt", b"alpha")
    second = _write_file(cache_root / "dataset" / "nested" / "b.txt", b"bravo")
    total_bytes = Path(first).stat().st_size + Path(second).stat().st_size
    return ArchivePlan(
        plan_id="plan-1",
        ingest_mode=IngestMode.CACHE_DRIVE,
        source_path=str(cache_root / "dataset"),
        pool="pool-1",
        volume_group="vg-1",
        files=[first, second],
        total_files=2,
        total_bytes=total_bytes,
        tape_assignments=[
            TapeAssignment(
                barcode="VOL001L9",
                files=["a.txt", "nested/b.txt"],
                estimated_bytes=total_bytes,
            )
        ],
    )


def _make_source_stream_plan() -> ArchivePlan:
    return ArchivePlan(
        plan_id="plan-source",
        ingest_mode=IngestMode.SOURCE_STREAM,
        source_path=None,
        pool="pool-1",
        volume_group="vg-1",
        files=["a.txt", "nested/b.txt"],
        total_files=2,
        total_bytes=2048,
        tape_assignments=[
            TapeAssignment(
                barcode="VOL001L9",
                files=["a.txt", "nested/b.txt"],
                estimated_bytes=2048,
            )
        ],
    )


def _run_job(service: NasService, job_id: str, *, cache_drive_id: str | None = "cache-1"):
    context = get_context()
    return run_ingest_job(
        job_id,
        nas_service=service,
        library=context.library,
        ltfs=context.ltfs,
        cache_drive_id=cache_drive_id,
    )


def test_cache_drive_ingest_creates_dataset(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = _make_plan(cache_root)

    job = start_ingest_job(
        plan=register_archive_plan(plan),
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    dataset = service.get_dataset(job.dataset_id)
    assert dataset is not None
    assert dataset.status is DatasetStatus.ARCHIVING
    assert dataset.file_count == 2


def test_cache_drive_ingest_completes(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    _run_job(service, job.job_id)

    dataset = service.get_dataset(job.dataset_id)
    assert dataset is not None
    assert dataset.status is DatasetStatus.ARCHIVED
    records = service.list_file_records(job.dataset_id)
    assert len(records) == 2
    assert {record.status for record in records} == {NasFileState.OFFLINE_ON_TAPE}


def test_cache_drive_ingest_marks_partial_success(tmp_path: Path, monkeypatch) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    context = get_context()
    write_bytes = context.ltfs.write_bytes

    def flaky_write(handle, dest, content, **kwargs):
        if str(dest).endswith("nested/b.txt"):
            raise RuntimeError("simulated per-file failure")
        return write_bytes(handle, dest, content, **kwargs)

    monkeypatch.setattr(context.ltfs, "write_bytes", flaky_write)

    result = _run_job(service, job.job_id)

    dataset = service.get_dataset(job.dataset_id)
    assert dataset is not None
    assert result.status is DatasetStatus.ARCHIVED
    assert dataset.status is DatasetStatus.ARCHIVED
    assert result.partial_success is True
    assert result.files_processed == 1
    assert result.files_failed == 1
    assert any("Archived with partial success" in error for error in result.errors)

    records = {record.relative_path: record for record in service.list_file_records(job.dataset_id)}
    assert records["a.txt"].status is NasFileState.OFFLINE_ON_TAPE
    assert records["nested/b.txt"].status is NasFileState.FAILED


def test_cancelled_ingest_marks_dataset_cancelled(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    assert cancel_ingest_job(job.job_id) is True

    result = _run_job(service, job.job_id)

    dataset = service.get_dataset(job.dataset_id)
    assert dataset is not None
    assert result.status is DatasetStatus.CANCELLED
    assert dataset.status is DatasetStatus.CANCELLED
    assert any("Cancelled by user" in error for error in result.errors)


def test_source_stream_ingest_adds_preflight_warning(tmp_path: Path) -> None:
    service, _ = _setup_service(tmp_path)
    plan = register_archive_plan(_make_source_stream_plan())
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
    )

    result = _run_job(service, job.job_id, cache_drive_id=None)

    assert result.status is DatasetStatus.ARCHIVED
    assert any(
        "No source_path set; streaming a.txt without source validation" in error
        for error in result.errors
    )


def test_ingest_updates_tape_set(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    _run_job(service, job.job_id)

    dataset = service.get_dataset(job.dataset_id)
    assert dataset is not None
    assert dataset.tape_set == ["VOL001L9"]


def test_ingest_file_records_have_checksums(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    _run_job(service, job.job_id)

    records = service.list_file_records(job.dataset_id)
    assert records
    assert all(record.checksum_sha256 for record in records)


def test_ingest_endpoint_returns_job_id(tmp_path: Path) -> None:
    _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(tmp_path / "cache"))

    response = client.post(
        "/nas/ingest/start",
        json={
            "plan_id": plan.plan_id,
            "dataset_name": "dataset-a",
            "pool_id": "pool-1",
            "cache_drive_id": "cache-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"]
    assert payload["dataset_id"]
    assert payload["status"] == "running"
    assert get_ingest_job(payload["job_id"]) is not None


def test_ingest_status_endpoint(tmp_path: Path) -> None:
    _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(tmp_path / "cache"))

    start_response = client.post(
        "/nas/ingest/start",
        json={
            "plan_id": plan.plan_id,
            "dataset_name": "dataset-a",
            "pool_id": "pool-1",
            "cache_drive_id": "cache-1",
        },
    )
    job_id = start_response.json()["job_id"]

    response = client.get(f"/nas/ingest/{job_id}")

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] in {"archiving", "archived", "failed", "cancelled"}


def test_ingest_with_no_plan_returns_400(tmp_path: Path) -> None:
    _setup_service(tmp_path)

    response = client.post(
        "/nas/ingest/start",
        json={
            "plan_id": "missing-plan",
            "dataset_name": "dataset-a",
            "pool_id": "pool-1",
            "cache_drive_id": "cache-1",
        },
    )

    assert response.status_code == 400
