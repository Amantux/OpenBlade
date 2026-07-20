from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.catalog.models import Cartridge
from openblade.catalog.models import NasDataset as NasDatasetRow
from openblade.catalog.models import NasFileRecord as NasFileRecordRow
from openblade.catalog.models import PathMapping as PathMappingRow
from openblade.config import OpenBladeConfig
from openblade.nas.catalog_rebuild import CatalogRebuildPlanner
from openblade.nas.catalog_rebuild_worker import SAFE_REBUILD_PREFLIGHT_ERROR, CatalogRebuildWorker
from openblade.nas.catalog_shard import (
    CatalogShard,
    CatalogShardDatasetEntry,
    CatalogShardFileEntry,
    CatalogShardWriter,
)
from openblade.nas.ltfs_manifest import (
    ManifestFileEntry,
    ManifestJson,
    TapeJson,
    TapeMetadataWriter,
)
from openblade.nas.manifest_validator import ManifestValidator
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.service import NasService
from openblade.nas.types import (
    DatasetStatus,
    IngestMode,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
    RebuildRunStatus,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> dict[str, object]:
    client.cookies.clear()
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'catalog-rebuild-worker.db'}"))
    reset_context(context)
    context.catalog.session.query(Cartridge).delete()
    context.catalog.session.commit()
    service = NasService(context.catalog)
    service.upsert_pool(NasPool(id="pool-1", name="pool-1"))
    metadata_writer = TapeMetadataWriter(context.ltfs)
    shard_writer = CatalogShardWriter(metadata_writer)
    validator = ManifestValidator(metadata_writer, shard_writer)
    path_mapping = PathMappingService(context.catalog)
    planner = CatalogRebuildPlanner(
        repo=context.catalog,
        metadata_writer=metadata_writer,
        shard_writer=shard_writer,
        manifest_validator=validator,
        path_mapping_service=path_mapping,
    )
    worker = CatalogRebuildWorker(repo=context.catalog, planner=planner)
    return {
        "context": context,
        "service": service,
        "metadata_writer": metadata_writer,
        "shard_writer": shard_writer,
        "validator": validator,
        "planner": planner,
        "worker": worker,
        "path_mapping": path_mapping,
    }


@pytest.fixture
def rebuild_env(reset_app_context: dict[str, object]) -> dict[str, object]:
    return reset_app_context


def _login(c: TestClient) -> None:
    response = c.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def _seed_loaded_cartridges(rebuild_env: dict[str, object], *barcodes: str) -> None:
    for barcode in barcodes:
        rebuild_env["context"].catalog.add_cartridge(barcode)


def _seed_tape(
    rebuild_env: dict[str, object],
    barcode: str,
    *,
    include_manifest: bool = True,
    valid_manifest: bool = True,
    include_shard: bool = True,
    files_per_tape: int = 2,
) -> dict[str, object]:
    metadata_writer: TapeMetadataWriter = rebuild_env["metadata_writer"]
    shard_writer: CatalogShardWriter = rebuild_env["shard_writer"]
    dataset_id = f"dataset-{barcode}"
    pool_id = "pool-1"
    metadata_writer.write_tape_json(
        barcode,
        TapeJson(
            openblade_tape_id=barcode,
            barcode=barcode,
            created_at="2025-01-01T00:00:00Z",
            last_openblade_write_at="2025-01-01T00:00:00Z",
            volume_group="vg-1",
            pools=[pool_id],
        ),
    )

    manifest_files = [
        ManifestFileEntry(
            logical_path=f"/{barcode.lower()}/file-{index}.dat",
            tape_path=f"/{barcode.lower()}/file-{index}.dat",
            dataset_id=dataset_id,
            file_record_id=f"{barcode}-file-{index}",
            size=10 + index,
            mtime="2025-01-01T00:00:00Z",
            checksum=f"checksum-{barcode}-{index}",
            verified=True,
        )
        for index in range(files_per_tape)
    ]
    manifest = ManifestJson(
        barcode=barcode,
        openblade_tape_id=barcode,
        volume_group="vg-1",
        pools=[pool_id],
        datasets=[dataset_id],
        files=manifest_files,
    )
    if include_manifest:
        if valid_manifest:
            checksum = metadata_writer.write_manifest(barcode, manifest)
            metadata_writer.write_manifest_checksum(barcode, checksum)
        else:
            payload = manifest.model_dump(by_alias=True)
            payload["file_count"] = len(manifest_files) + 5
            metadata_writer._write_json(barcode, "/.openblade/manifest.json", payload)
            metadata_writer._write_text(
                barcode,
                "/.openblade/manifest.sha256",
                metadata_writer.compute_json_checksum(payload),
            )

    shard_files = [
        CatalogShardFileEntry(
            logical_path=entry.logical_path,
            tape_path=entry.tape_path,
            dataset_id=entry.dataset_id,
            file_record_id=entry.file_record_id,
            pool_id=pool_id,
            size=entry.size,
            mtime=entry.mtime,
            checksum=entry.checksum,
            file_state=NasFileState.OFFLINE_ON_TAPE.value,
            verified=True,
        )
        for entry in manifest_files
    ]
    shard = CatalogShard(
        barcode=barcode,
        openblade_tape_id=barcode,
        volume_group="vg-1",
        generated_at="2025-01-01T00:00:00Z",
        datasets=[
            CatalogShardDatasetEntry(
                dataset_id=dataset_id,
                pool_id=pool_id,
                volume_group="vg-1",
                policy="critical_sequential",
                ingest_mode="cache_drive",
                file_count=len(shard_files),
                total_bytes=sum(item.size for item in shard_files),
                tape_set=[barcode],
                shard_set=[],
            )
        ],
        files=shard_files,
    )
    if include_shard:
        shard_writer.write_shard(barcode, shard)

    return {
        "barcode": barcode,
        "dataset_id": dataset_id,
        "manifest": manifest,
        "shard": shard,
    }


def _populate_catalog_from_seed(rebuild_env: dict[str, object], seeded: dict[str, object]) -> None:
    service: NasService = rebuild_env["service"]
    shard_writer: CatalogShardWriter = rebuild_env["shard_writer"]
    path_mapping: PathMappingService = rebuild_env["path_mapping"]
    shard: CatalogShard = seeded["shard"]
    barcode = seeded["barcode"]

    for dataset_entry in shard.datasets:
        service.upsert_dataset(
            NasDataset(
                id=dataset_entry.dataset_id,
                pool_id=dataset_entry.pool_id,
                name=dataset_entry.dataset_id,
                policy_id=dataset_entry.policy,
                ingest_mode=IngestMode.CACHE_DRIVE,
                volume_group_id=dataset_entry.volume_group,
                tape_set=[barcode],
                file_count=dataset_entry.file_count,
                total_bytes=dataset_entry.total_bytes,
                status=DatasetStatus.ARCHIVED,
                copies_completed=1,
                manifest_path="/.openblade/manifest.json",
            )
        )

    for file_entry in shard.files:
        service.upsert_file_record(
            NasFileRecord(
                id=file_entry.file_record_id,
                dataset_id=file_entry.dataset_id,
                pool_id=file_entry.pool_id,
                relative_path=file_entry.logical_path,
                size_bytes=file_entry.size,
                mtime=file_entry.mtime,
                checksum_sha256=file_entry.checksum,
                tape_barcode=barcode,
                status=NasFileState.OFFLINE_ON_TAPE,
            )
        )

    for record in shard_writer.shard_to_path_mappings(shard):
        path_mapping.record_file(record)


def test_auto_plan_and_execute_succeeds(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    seeded = _seed_tape(rebuild_env, barcode)

    result = rebuild_env["worker"].auto_plan_and_execute([barcode], triggered_by="operator")

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.barcodes_completed == [barcode]
    assert result.files_recovered == len(seeded["shard"].files)
    assert result.datasets_recovered == len(seeded["shard"].datasets)
    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcode}-file-0") is not None


def test_auto_plan_and_execute_dry_run_first_blocks_invalid(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)

    with pytest.raises(ValueError, match=SAFE_REBUILD_PREFLIGHT_ERROR):
        rebuild_env["worker"].auto_plan_and_execute([barcode], triggered_by="operator")



def test_auto_plan_and_execute_no_dry_run_first_skips_preflight(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)

    result = rebuild_env["worker"].auto_plan_and_execute(
        [barcode],
        triggered_by="operator",
        dry_run_first=False,
    )

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.barcodes_completed == []
    assert result.files_recovered == 0



def test_recover_from_loaded_tapes_uses_library_barcodes(rebuild_env: dict[str, object]) -> None:
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:3]
    _seed_tape(rebuild_env, barcodes[0])
    _seed_tape(rebuild_env, barcodes[1])
    _seed_tape(rebuild_env, barcodes[2])
    _seed_loaded_cartridges(rebuild_env, barcodes[0], barcodes[1])

    result = rebuild_env["worker"].recover_from_loaded_tapes(triggered_by="operator")

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.barcodes_completed == [barcodes[0], barcodes[1]]
    assert barcodes[2] not in result.barcodes_completed



def test_recover_from_loaded_tapes_empty_library_returns_empty_run(rebuild_env: dict[str, object]) -> None:
    result = rebuild_env["worker"].recover_from_loaded_tapes(triggered_by="operator")

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.barcodes_completed == []
    assert result.files_recovered == 0



def test_rebuild_status_returns_none_for_unknown(rebuild_env: dict[str, object]) -> None:
    assert rebuild_env["worker"].rebuild_status("missing-run") is None



def test_rebuild_status_returns_record(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    run = rebuild_env["worker"].auto_plan_and_execute([barcode], triggered_by="operator")

    stored = rebuild_env["worker"].rebuild_status(run.id)

    assert stored is not None
    assert stored.id == run.id
    assert stored.status is RebuildRunStatus.COMPLETED



def test_lost_db_recovery_full_scenario(rebuild_env: dict[str, object]) -> None:
    """
    Simulate: operator loses DB, reloads tapes, triggers rebuild.
    1. Set up catalog with 2 tapes (T1, T2), each with files, run ingest
    2. Wipe the catalog DB (delete all NasFileRecord + NasDataset rows)
    3. Verify files are gone from catalog
    4. Run rebuild worker on [T1, T2]
    5. Verify files are back in catalog
    6. Verify path mappings are restored
    7. Verify dataset tape_set is correct
    """
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    seeded_one = _seed_tape(rebuild_env, barcodes[0])
    seeded_two = _seed_tape(rebuild_env, barcodes[1])
    _populate_catalog_from_seed(rebuild_env, seeded_one)
    _populate_catalog_from_seed(rebuild_env, seeded_two)

    assert rebuild_env["service"].list_file_records(seeded_one["dataset_id"])
    assert rebuild_env["service"].list_file_records(seeded_two["dataset_id"])
    assert rebuild_env["context"].catalog.count_path_mappings() == 4

    session = rebuild_env["context"].catalog.session
    session.query(PathMappingRow).delete()
    session.query(NasFileRecordRow).delete()
    session.query(NasDatasetRow).delete()
    session.commit()

    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcodes[0]}-file-0") is None
    assert rebuild_env["context"].catalog.get_nas_dataset(seeded_one["dataset_id"]) is None
    assert rebuild_env["context"].catalog.count_path_mappings() == 0

    result = rebuild_env["worker"].auto_plan_and_execute(barcodes, triggered_by="operator")

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.files_recovered == 4
    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcodes[0]}-file-0") is not None
    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcodes[1]}-file-1") is not None
    assert rebuild_env["path_mapping"].lookup(seeded_one["manifest"].files[0].logical_path, "pool-1").found is True
    assert rebuild_env["path_mapping"].lookup(seeded_two["manifest"].files[1].logical_path, "pool-1").found is True
    assert rebuild_env["context"].catalog.get_nas_dataset(seeded_one["dataset_id"])["tape_set"] == [barcodes[0]]
    assert rebuild_env["context"].catalog.get_nas_dataset(seeded_two["dataset_id"])["tape_set"] == [barcodes[1]]



def test_api_activate_requires_auth(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    anon = TestClient(app)

    response = anon.post("/nas/catalog/rebuild/activate", json={"barcodes": [barcode]})

    assert response.status_code in (401, 403)



def test_api_activate_with_barcodes(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    _login(client)

    response = client.post(
        "/nas/catalog/rebuild/activate",
        json={"barcodes": [barcode], "triggered_by": "operator"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == RebuildRunStatus.COMPLETED.value
    assert response.json()["files_recovered"] == 2
    assert response.json()["safe_to_enqueue"] is True



def test_api_activate_loaded_tapes(rebuild_env: dict[str, object]) -> None:
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    _seed_tape(rebuild_env, barcodes[0])
    _seed_tape(rebuild_env, barcodes[1])
    _seed_loaded_cartridges(rebuild_env, barcodes[0], barcodes[1])
    _login(client)

    response = client.post("/nas/catalog/rebuild/activate", json={"triggered_by": "operator"})

    assert response.status_code == 200
    assert response.json()["barcodes_completed"] == barcodes
    assert response.json()["datasets_recovered"] == 2



def test_api_activate_returns_422_when_not_safe(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)
    _login(client)

    response = client.post(
        "/nas/catalog/rebuild/activate",
        json={"barcodes": [barcode], "triggered_by": "operator"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["safe_to_enqueue"] is False
    assert response.json()["detail"]["message"] == SAFE_REBUILD_PREFLIGHT_ERROR
    assert barcode not in response.json()["detail"]["message"]



def test_api_loaded_tapes_requires_auth() -> None:
    anon = TestClient(app)

    response = anon.get("/nas/catalog/rebuild/loaded-tapes")

    assert response.status_code in (401, 403)



def test_api_loaded_tapes_returns_barcodes(rebuild_env: dict[str, object]) -> None:
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    _seed_loaded_cartridges(rebuild_env, barcodes[1], barcodes[0])
    _login(client)

    response = client.get("/nas/catalog/rebuild/loaded-tapes")

    assert response.status_code == 200
    assert response.json() == sorted(barcodes)



def test_dry_run_first_raises_when_not_safe_to_enqueue(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)

    with pytest.raises(ValueError) as exc_info:
        rebuild_env["worker"].auto_plan_and_execute([barcode], triggered_by="operator")

    assert str(exc_info.value) == SAFE_REBUILD_PREFLIGHT_ERROR
    assert barcode not in str(exc_info.value)


def test_rebuild_with_a_missing_tape_recovers_available_subset(
    rebuild_env: dict[str, object],
) -> None:
    """DR with a MISSING tape (roadmap #11): rebuilding from only the tapes actually
    available recovers their files and gracefully leaves the absent tape's files
    unrecovered — no crash, run still completes."""
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    seeded_one = _seed_tape(rebuild_env, barcodes[0])
    seeded_two = _seed_tape(rebuild_env, barcodes[1])
    _populate_catalog_from_seed(rebuild_env, seeded_one)
    _populate_catalog_from_seed(rebuild_env, seeded_two)

    session = rebuild_env["context"].catalog.session
    session.query(PathMappingRow).delete()
    session.query(NasFileRecordRow).delete()
    session.query(NasDatasetRow).delete()
    session.commit()

    # Only tape 0 is available for the rebuild; tape 1 is "missing".
    result = rebuild_env["worker"].auto_plan_and_execute([barcodes[0]], triggered_by="operator")

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.files_recovered == 2  # tape 0's files only
    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcodes[0]}-file-0") is not None
    # The missing tape's records remain unrecovered — no silent fabrication, no crash.
    assert rebuild_env["context"].catalog.get_nas_file_record(f"{barcodes[1]}-file-1") is None
