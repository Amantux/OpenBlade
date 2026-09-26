import hashlib
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
    SourceStreamConfig,
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


def _make_source_stream_plan(source_root: Path | None = None) -> ArchivePlan:
    return ArchivePlan(
        plan_id="plan-source",
        ingest_mode=IngestMode.SOURCE_STREAM,
        source_path=None if source_root is None else str(source_root),
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


def test_cache_drive_ingest_fails_when_capacity_budget_is_exceeded(tmp_path: Path) -> None:
    service, _ = _setup_service(tmp_path)
    service.upsert_cache_drive(
        CacheDriveConfig(
            id="cache-1",
            name="Cache 1",
            root_path=str(tmp_path / "cache"),
            max_bytes=8,
            min_free_bytes=4,
        )
    )
    plan = register_archive_plan(_make_plan(tmp_path / "cache"))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    result = _run_job(service, job.job_id)

    assert result.status is DatasetStatus.FAILED
    assert any("cannot reserve" in error for error in result.errors)


def test_source_stream_ingest_requires_source_path_by_default(tmp_path: Path) -> None:
    service, _ = _setup_service(tmp_path)
    plan = register_archive_plan(_make_source_stream_plan())
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
    )

    result = _run_job(service, job.job_id, cache_drive_id=None)

    assert result.status is DatasetStatus.FAILED
    assert any("source_path is required" in error for error in result.errors)


def test_source_stream_ingest_detects_source_changes(tmp_path: Path, monkeypatch) -> None:
    service, _ = _setup_service(tmp_path)
    stream_root = tmp_path / "stream"
    _write_file(stream_root / "a.txt", b"alpha")
    _write_file(stream_root / "nested" / "b.txt", b"bravo")
    service.update_source_stream_config(
        SourceStreamConfig(
            enabled=True,
            require_source_online_for_entire_job=True,
            preflight_read_check=True,
            fail_on_source_change=True,
            checksum_mode="precompute_and_post_verify",
        )
    )
    plan = register_archive_plan(_make_source_stream_plan(stream_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
    )

    context = get_context()
    write_bytes = context.ltfs.write_bytes
    mutated = {"done": False}

    def mutate_source_after_first_write(handle, dest, content, **kwargs):
        result = write_bytes(handle, dest, content, **kwargs)
        if not mutated["done"]:
            (stream_root / "nested" / "b.txt").write_bytes(b"changed")
            mutated["done"] = True
        return result

    monkeypatch.setattr(context.ltfs, "write_bytes", mutate_source_after_first_write)

    result = _run_job(service, job.job_id, cache_drive_id=None)

    assert result.status is DatasetStatus.FAILED
    assert any("Source changed during source-stream ingest" in error for error in result.errors)


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


def test_large_file_ingest_and_verify_dataset(tmp_path: Path) -> None:
    service, cache_root = _setup_service(tmp_path)
    service.upsert_cache_drive(
        CacheDriveConfig(
            id="cache-1",
            name="Cache 1",
            root_path=str(cache_root),
            max_bytes=20_000_000,
            min_free_bytes=0,
        )
    )
    large_content = b"X" * 8_388_608
    source = _write_file(cache_root / "dataset" / "large.bin", large_content)
    plan = register_archive_plan(
        ArchivePlan(
            plan_id="plan-large",
            ingest_mode=IngestMode.CACHE_DRIVE,
            source_path=str(cache_root / "dataset"),
            pool="pool-1",
            volume_group="vg-1",
            files=[source],
            total_files=1,
            total_bytes=len(large_content),
            tape_assignments=[
                TapeAssignment(
                    barcode="VOL001L9",
                    files=["large.bin"],
                    estimated_bytes=len(large_content),
                )
            ],
        )
    )
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-large",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    result = _run_job(service, job.job_id)

    assert result.status is DatasetStatus.ARCHIVED
    records = service.list_file_records(job.dataset_id)
    assert len(records) == 1
    assert records[0].checksum_sha256 == hashlib.sha256(large_content).hexdigest()

    verify_response = client.post(f"/nas/datasets/{job.dataset_id}/verify")
    assert verify_response.status_code == 200
    verify_body = verify_response.json()
    assert verify_body["files_verified"] == 1
    assert verify_body["files_corrupt"] == 0


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


def test_ingest_run_failure_error_is_sanitized(tmp_path: Path, monkeypatch) -> None:
    """IngestJob.errors is served by GET /nas/ingest/{job_id}, a route with no auth
    dependency. run()'s catch-all owns the dataset's terminal state, so it must stay
    broad — but the text it records goes through safe_job_error(), or a CommandError
    publishes mkltfs/mtx argv and stderr on an unauthenticated surface."""
    from openblade.hardware.runner import CommandError
    from openblade.nas.ingest import CacheDriveIngest

    service, cache_root = _setup_service(tmp_path)
    plan = register_archive_plan(_make_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    def leaky(self):
        raise CommandError(["mkltfs", "-d", "/dev/nst0"], 1, "raw stderr LEAK")

    monkeypatch.setattr(CacheDriveIngest, "_prepare_files", leaky)

    result = _run_job(service, job.job_id)

    assert result.status is DatasetStatus.FAILED
    joined = " ".join(result.errors)
    assert "LEAK" not in joined
    assert "/dev/nst0" not in joined
    assert "mkltfs" not in joined
    assert "CommandError" in joined


def test_ingest_per_file_error_is_sanitized(tmp_path: Path, monkeypatch) -> None:
    """The per-file handler records into the same unauthenticated IngestJob.errors,
    so it goes through safe_job_error() too. The relative path stays: it names which
    file failed, which the dataset listing already shows."""
    from openblade.hardware.runner import CommandError

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

    def leaky_write(handle, dest, content, **kwargs):
        raise CommandError(["mkltfs", "-d", "/dev/nst0"], 1, "raw stderr LEAK")

    monkeypatch.setattr(context.ltfs, "write_bytes", leaky_write)

    result = _run_job(service, job.job_id)

    joined = " ".join(result.errors)
    assert result.files_failed == 2
    assert "LEAK" not in joined
    assert "/dev/nst0" not in joined
    assert "a.txt" in joined
    assert "CommandError" in joined


def test_preflight_refusals_keep_their_curated_text(tmp_path: Path) -> None:
    """IngestRefusedError is typed so safe_job_error passes it through: sanitizing
    the run() boundary must not degrade the messages an operator acts on."""
    from openblade.domain.errors import safe_job_error
    from openblade.nas.ingest import IngestRefusedError

    exc = IngestRefusedError("Cache drive cache-1 cannot reserve 10 bytes")
    assert safe_job_error(exc) == "Cache drive cache-1 cannot reserve 10 bytes"
    assert isinstance(exc, RuntimeError)
