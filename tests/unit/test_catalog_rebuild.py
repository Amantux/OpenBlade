from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from openblade.api.main import app
from openblade.bootstrap import create_context, reset_context
from openblade.config import OpenBladeConfig
from openblade.nas.catalog_rebuild import CatalogRebuildPlanner
from openblade.nas.catalog_shard import CatalogShard, CatalogShardDatasetEntry, CatalogShardFileEntry, CatalogShardWriter
from openblade.nas.ltfs_manifest import ManifestFileEntry, ManifestJson, TapeJson, TapeMetadataWriter
from openblade.nas.manifest_validator import ManifestValidator
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.service import NasService
from openblade.nas.types import (
    CatalogRebuildRunRecord,
    DatasetStatus,
    ManifestVersionRecord,
    NasFileState,
    NasPool,
    RebuildPlanRequest,
    RebuildRunStatus,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_app_context(tmp_path: Path) -> dict[str, object]:
    client.cookies.clear()
    context = create_context(OpenBladeConfig(db_url=f"sqlite:///{tmp_path / 'catalog-rebuild.db'}"))
    reset_context(context)
    service = NasService(context.catalog)
    service.upsert_pool(NasPool(id="pool-1", name="pool-1"))
    metadata_writer = TapeMetadataWriter(context.ltfs)
    shard_writer = CatalogShardWriter(metadata_writer)
    validator = ManifestValidator(metadata_writer, shard_writer)
    planner = CatalogRebuildPlanner(
        repo=context.catalog,
        metadata_writer=metadata_writer,
        shard_writer=shard_writer,
        manifest_validator=validator,
        path_mapping_service=PathMappingService(context.catalog),
    )
    return {
        "context": context,
        "service": service,
        "metadata_writer": metadata_writer,
        "shard_writer": shard_writer,
        "validator": validator,
        "planner": planner,
        "path_mapping": PathMappingService(context.catalog),
    }


@pytest.fixture
def rebuild_env(reset_app_context: dict[str, object]) -> dict[str, object]:
    return reset_app_context


def _login(c: TestClient) -> None:
    response = c.post("/aml/users/login", json={"name": "admin", "password": "password"})
    assert response.status_code == 200


def _seed_tape(
    rebuild_env: dict[str, object],
    barcode: str,
    *,
    include_manifest: bool = True,
    valid_manifest: bool = True,
    include_shard: bool = True,
    files_per_tape: int = 2,
    create_versions: int = 0,
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

    if create_versions:
        metadata_writer._ensure_dir(barcode, "/.openblade/versions")
        for index in range(create_versions):
            version_payload = manifest.model_dump(by_alias=True)
            version_payload["files"] = version_payload["files"][: index + 1]
            version_payload["file_count"] = index + 1
            path = f"/.openblade/versions/manifest.20250101T00000{index}Z.json"
            if index == create_versions - 1:
                version_payload = manifest.model_dump(by_alias=True)
            metadata_writer._write_json(barcode, path, version_payload)

    return {
        "barcode": barcode,
        "dataset_id": dataset_id,
        "manifest": manifest,
        "shard": shard,
    }


def test_plan_rebuild_dry_run_returns_counts(rebuild_env: dict[str, object]) -> None:
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    _seed_tape(rebuild_env, barcodes[0], files_per_tape=2)
    _seed_tape(rebuild_env, barcodes[1], files_per_tape=1)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=barcodes, dry_run=True))

    assert result.run_id == ""
    assert result.barcodes_to_scan == barcodes
    assert result.estimated_files == 3
    assert result.estimated_datasets == 2
    assert result.estimated_path_mappings == 3
    assert result.safe_to_enqueue is True


def test_plan_rebuild_missing_manifest_tracked(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, include_manifest=False)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=True))

    assert result.barcodes_missing_manifest == [barcode]
    assert result.barcodes_to_scan == []


def test_plan_rebuild_invalid_manifest_tracked(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=True))

    assert result.barcodes_invalid == [barcode]
    assert result.safe_to_enqueue is False


def test_plan_rebuild_missing_shard_tracked(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, include_shard=False)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=True))

    assert result.barcodes_missing_shard == [barcode]
    assert result.barcodes_to_scan == []


def test_plan_rebuild_creates_run_record_when_not_dry_run(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))
    stored = rebuild_env["context"].catalog.get_rebuild_run(result.run_id)

    assert result.run_id
    assert stored is not None
    assert stored["status"] == RebuildRunStatus.PLANNED.value
    assert stored["barcodes_planned"] == [barcode]


def test_plan_rebuild_safe_to_enqueue_false_when_invalid(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, valid_manifest=False)

    result = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))

    assert result.safe_to_enqueue is False


def test_execute_rebuild_restores_path_mappings(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    seeded = _seed_tape(rebuild_env, barcode)
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))

    rebuild_env["planner"].execute_rebuild_run(plan.run_id)
    lookup = rebuild_env["path_mapping"].lookup(
        seeded["manifest"].files[0].logical_path,
        "pool-1",
    )

    assert lookup.found is True
    assert lookup.primary_barcode == barcode


def test_execute_rebuild_counts_files_recovered(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, files_per_tape=3)
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))

    result = rebuild_env["planner"].execute_rebuild_run(plan.run_id)

    assert result.files_recovered == 3
    assert result.path_mappings_recovered == 3
    assert result.datasets_recovered == 1


def test_execute_rebuild_marks_run_completed(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))

    result = rebuild_env["planner"].execute_rebuild_run(plan.run_id)

    assert result.status is RebuildRunStatus.COMPLETED
    assert result.barcodes_completed == [barcode]
    assert result.completed_at is not None


def test_execute_rebuild_invalid_run_id_raises(rebuild_env: dict[str, object]) -> None:
    with pytest.raises(KeyError):
        rebuild_env["planner"].execute_rebuild_run("missing-run")


def test_execute_rebuild_fails_if_run_not_planned(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))
    rebuild_env["context"].catalog.update_rebuild_run(
        plan.run_id,
        {
            "status": RebuildRunStatus.RUNNING.value,
            "updated_at": "2025-01-01T00:00:01Z",
        },
    )

    with pytest.raises(ValueError):
        rebuild_env["planner"].execute_rebuild_run(plan.run_id)


def test_execute_rebuild_partial_failure_marks_failed(rebuild_env: dict[str, object]) -> None:
    barcodes = rebuild_env["context"].library.get_all_barcodes()[:2]
    _seed_tape(rebuild_env, barcodes[0])
    _seed_tape(rebuild_env, barcodes[1])
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=barcodes, dry_run=False))
    rebuild_env["context"].ltfs.ensure_tape(barcodes[1]).files.pop("/.openblade/catalog-shard.json", None)

    result = rebuild_env["planner"].execute_rebuild_run(plan.run_id)

    assert result.status is RebuildRunStatus.FAILED
    assert result.barcodes_completed == [barcodes[0]]
    assert result.barcodes_failed == [barcodes[1]]
    assert result.error_summary == [f"catalog shard unavailable for {barcodes[1]}"]


def test_list_manifest_versions_returns_versions(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode, create_versions=2)
    plan = rebuild_env["planner"].plan_rebuild(RebuildPlanRequest(barcodes=[barcode], dry_run=False))

    rebuild_env["planner"].execute_rebuild_run(plan.run_id)
    versions = [
        ManifestVersionRecord.model_validate(item)
        for item in rebuild_env["context"].catalog.list_manifest_versions(barcode)
    ]

    assert len(versions) == 2
    assert any(version.is_current for version in versions)
    assert versions[0].manifest_path.startswith("/.openblade/versions/manifest.")


def test_api_plan_rebuild_requires_auth(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    anon = TestClient(app)

    response = anon.post("/nas/catalog/rebuild/plan", json={"barcodes": [barcode], "dry_run": True})

    assert response.status_code in (401, 403)


def test_api_execute_rebuild_requires_auth() -> None:
    anon = TestClient(app)

    response = anon.post("/nas/catalog/rebuild/run-1/execute")

    assert response.status_code in (401, 403)


def test_api_get_run_returns_record(rebuild_env: dict[str, object]) -> None:
    barcode = rebuild_env["context"].library.get_all_barcodes()[0]
    _seed_tape(rebuild_env, barcode)
    _login(client)

    plan_response = client.post("/nas/catalog/rebuild/plan", json={"barcodes": [barcode], "dry_run": False})
    run_id = plan_response.json()["run_id"]
    response = client.get(f"/nas/catalog/rebuild/{run_id}")

    assert plan_response.status_code == 200
    assert response.status_code == 200
    assert response.json()["id"] == run_id
    assert response.json()["status"] == RebuildRunStatus.PLANNED.value
