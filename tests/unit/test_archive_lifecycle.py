from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.archive_lifecycle import ArchiveLifecycleManager
from openblade.nas.catalog_shard import CatalogShardWriter
from openblade.nas.ingest import clear_ingest_state, register_archive_plan, run_ingest_job, start_ingest_job
from openblade.nas.ltfs_manifest import TapeJson, TapeMetadataWriter
from openblade.nas.manifest_validator import ManifestValidator, VersionedManifestWriter
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.service import NasService
from openblade.nas.types import (
    ArchivePlan,
    CacheDriveConfig,
    DatasetStatus,
    IngestMode,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
    TapeAssignment,
)


BARCODE = "VOL001L9"


@pytest.fixture
def archive_env(tmp_path: Path):
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'archive-lifecycle.db'}"))
    reset_context(context)
    clear_ingest_state()
    service = NasService(context.catalog)
    service.upsert_pool(NasPool(id="pool-1", name="pool-1"))
    dataset = service.upsert_dataset(
        NasDataset(
            id="dataset-1",
            pool_id="pool-1",
            name="dataset-a",
            source_path="/src/dataset-a",
            policy_id="critical_sequential",
            ingest_mode=IngestMode.CACHE_DRIVE,
            volume_group_id="vg-1",
            status=DatasetStatus.ARCHIVING,
        )
    )
    context.catalog.add_cartridge(BARCODE)
    metadata_writer = TapeMetadataWriter(context.ltfs)
    shard_writer = CatalogShardWriter(metadata_writer)
    validator = ManifestValidator(metadata_writer, shard_writer)
    manager = ArchiveLifecycleManager(
        repo=context.catalog,
        metadata_writer=metadata_writer,
        shard_writer=shard_writer,
        versioned_manifest_writer=VersionedManifestWriter(metadata_writer),
        manifest_validator=validator,
        path_mapping_service=PathMappingService(context.catalog),
    )
    return {
        "context": context,
        "service": service,
        "dataset": dataset,
        "manager": manager,
        "metadata_writer": metadata_writer,
        "shard_writer": shard_writer,
        "validator": validator,
        "path_mapping": PathMappingService(context.catalog),
    }


def _seed_file(
    archive_env,
    *,
    relative_path: str = "a.txt",
    payload: bytes = b"alpha",
    status: NasFileState = NasFileState.ONLINE_CACHED,
) -> tuple[NasFileRecord, str, bytes]:
    service: NasService = archive_env["service"]
    dataset: NasDataset = archive_env["dataset"]
    checksum = hashlib.sha256(payload).hexdigest()
    tape_path = f"/{dataset.name}/{relative_path}"
    archive_env["context"].ltfs.write_bytes(BARCODE, tape_path, payload)
    file_record = service.upsert_file_record(
        NasFileRecord(
            dataset_id=dataset.id,
            pool_id=dataset.pool_id,
            relative_path=relative_path,
            source_path=f"/src/{relative_path}",
            size_bytes=len(payload),
            mtime="2025-01-01T00:00:00Z",
            checksum_sha256=checksum,
            tape_barcode=BARCODE,
            status=status,
        )
    )
    return file_record, tape_path, payload


def _seed_two_files(archive_env) -> tuple[list[NasFileRecord], dict[str, str]]:
    first, first_path, _ = _seed_file(archive_env, relative_path="a.txt", payload=b"alpha")
    second, second_path, _ = _seed_file(
        archive_env,
        relative_path="nested/b.txt",
        payload=b"bravo",
    )
    return [first, second], {first.id: first_path, second.id: second_path}


def _make_ingest_plan(cache_root: Path) -> ArchivePlan:
    dataset_root = cache_root / "dataset"
    (dataset_root / "nested").mkdir(parents=True, exist_ok=True)
    (dataset_root / "a.txt").write_bytes(b"alpha")
    (dataset_root / "nested" / "b.txt").write_bytes(b"bravo")
    return ArchivePlan(
        plan_id="plan-archive-lifecycle",
        policy_name="critical_sequential",
        ingest_mode=IngestMode.CACHE_DRIVE,
        source_path=str(dataset_root),
        pool="pool-1",
        volume_group="vg-1",
        files=[str(dataset_root / "a.txt"), str(dataset_root / "nested" / "b.txt")],
        total_files=2,
        total_bytes=10,
        tape_assignments=[
            TapeAssignment(
                barcode=BARCODE,
                files=["a.txt", "nested/b.txt"],
                estimated_bytes=10,
            )
        ],
    )


def test_complete_file_archive_returns_success_with_all_steps(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    result = archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path, "critical")

    assert result.success is True
    assert result.steps_completed == [
        "verify_checksum",
        "write_manifest",
        "write_catalog_shard",
        "update_path_mapping",
        "update_tape_json",
        "mark_file_archived",
    ]
    assert result.steps_failed == []


def test_complete_file_archive_sets_final_file_state_offline_on_tape(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    result = archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    assert result.final_file_state == "offline_on_tape"
    refreshed = archive_env["service"].get_file_record(file_record.id)
    assert refreshed is not None and refreshed.status is NasFileState.OFFLINE_ON_TAPE


def test_complete_file_archive_records_path_mapping_lookup(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    lookup = archive_env["path_mapping"].lookup(file_record.relative_path, file_record.pool_id or "")
    assert lookup.found is True
    assert lookup.primary_barcode == BARCODE


def test_complete_file_archive_writes_valid_manifest(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    result = archive_env["validator"].validate_manifest(BARCODE)
    assert result.valid is True


def test_complete_file_archive_writes_catalog_shard_to_tape(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    shard = archive_env["shard_writer"].read_shard(BARCODE)
    assert shard is not None
    assert [entry.file_record_id for entry in shard.files] == [file_record.id]


def test_complete_file_archive_updates_tape_json_timestamp(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)
    archive_env["metadata_writer"].write_tape_json(
        BARCODE,
        TapeJson(
            openblade_tape_id=BARCODE,
            barcode=BARCODE,
            created_at="2025-01-01T00:00:00Z",
            last_openblade_write_at="2025-01-01T00:00:00Z",
        ),
    )

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    tape_json = archive_env["metadata_writer"].read_tape_json(BARCODE)
    assert tape_json is not None
    assert tape_json.last_openblade_write_at != "2025-01-01T00:00:00Z"


def test_complete_file_archive_stops_before_manifest_when_verify_fails(
    archive_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)
    monkeypatch.setattr(archive_env["manager"], "_step_verify_checksum", lambda *args: False)

    result = archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    assert result.success is False
    assert result.steps_completed == []
    assert result.steps_failed == ["verify_checksum"]
    assert archive_env["metadata_writer"].read_manifest(BARCODE) is None


def test_complete_dataset_archive_succeeds_when_all_files_succeed(archive_env) -> None:
    file_records, tape_paths = _seed_two_files(archive_env)

    result = archive_env["manager"].complete_dataset_archive(
        archive_env["dataset"].id,
        file_records,
        BARCODE,
        tape_paths,
        "critical",
    )

    assert result.success is True
    assert result.files_completed == 2
    assert result.files_failed == 0


def test_complete_dataset_archive_marks_dataset_archived_only_when_all_files_succeed(archive_env) -> None:
    file_records, tape_paths = _seed_two_files(archive_env)

    result = archive_env["manager"].complete_dataset_archive(
        archive_env["dataset"].id,
        file_records,
        BARCODE,
        tape_paths,
    )

    dataset = archive_env["service"].get_dataset(archive_env["dataset"].id)
    assert result.dataset_marked_archived is True
    assert dataset is not None and dataset.status is DatasetStatus.ARCHIVED


def test_complete_dataset_archive_does_not_mark_dataset_archived_when_any_file_fails(archive_env) -> None:
    file_records, tape_paths = _seed_two_files(archive_env)
    archive_env["context"].ltfs.ensure_tape(BARCODE).files.pop(tape_paths[file_records[1].id])

    result = archive_env["manager"].complete_dataset_archive(
        archive_env["dataset"].id,
        file_records,
        BARCODE,
        tape_paths,
    )

    dataset = archive_env["service"].get_dataset(archive_env["dataset"].id)
    assert result.dataset_marked_archived is False
    assert dataset is not None and dataset.status is DatasetStatus.ARCHIVING


def test_complete_dataset_archive_reports_completed_and_failed_counts(archive_env) -> None:
    file_records, tape_paths = _seed_two_files(archive_env)
    archive_env["context"].ltfs.ensure_tape(BARCODE).files.pop(tape_paths[file_records[1].id])

    result = archive_env["manager"].complete_dataset_archive(
        archive_env["dataset"].id,
        file_records,
        BARCODE,
        tape_paths,
    )

    assert result.files_completed == 1
    assert result.files_failed == 1


def test_ingest_job_marks_file_records_offline_on_tape_after_lifecycle(tmp_path: Path) -> None:
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'ingest-lifecycle.db'}"))
    reset_context(context)
    clear_ingest_state()
    service = NasService(context.catalog)
    service.upsert_pool(NasPool(id="pool-1", name="pool-1"))
    cache_root = tmp_path / "cache"
    service.upsert_cache_drive(
        CacheDriveConfig(
            id="cache-1",
            name="Cache 1",
            root_path=str(cache_root),
            max_bytes=1_000_000,
            min_free_bytes=0,
        )
    )
    plan = register_archive_plan(_make_ingest_plan(cache_root))
    job = start_ingest_job(
        plan=plan,
        dataset_name="dataset-a",
        pool_id="pool-1",
        nas_service=service,
        cache_drive_id="cache-1",
    )

    result = run_ingest_job(
        job.job_id,
        nas_service=service,
        library=context.library,
        ltfs=context.ltfs,
        cache_drive_id="cache-1",
    )

    records = service.list_file_records(job.dataset_id)
    assert result.status is DatasetStatus.ARCHIVED
    assert {record.status for record in records} == {NasFileState.OFFLINE_ON_TAPE}


def test_complete_file_archive_populates_path_mapping_record_fields(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    lookup = archive_env["path_mapping"].lookup(file_record.relative_path, file_record.pool_id or "")
    assert lookup.dataset_id == archive_env["dataset"].id
    assert lookup.checksum == file_record.checksum_sha256


def test_complete_dataset_archive_returns_one_file_result_per_file(archive_env) -> None:
    file_records, tape_paths = _seed_two_files(archive_env)

    result = archive_env["manager"].complete_dataset_archive(
        archive_env["dataset"].id,
        file_records,
        BARCODE,
        tape_paths,
    )

    assert len(result.file_results) == 2


def test_manifest_round_trip_contains_archived_file_entry(archive_env) -> None:
    file_record, tape_path, _ = _seed_file(archive_env)

    archive_env["manager"].complete_file_archive(file_record, BARCODE, tape_path)

    manifest = archive_env["metadata_writer"].read_manifest(BARCODE)
    assert manifest is not None
    assert [entry.file_record_id for entry in manifest.files] == [file_record.id]
