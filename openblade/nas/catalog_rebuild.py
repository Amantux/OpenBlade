"""Catalog rebuild planning and execution from on-tape metadata."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from openblade.catalog.repository import CatalogRepository
from openblade.nas.catalog_shard import CatalogShard, CatalogShardDatasetEntry, CatalogShardFileEntry, CatalogShardWriter
from openblade.nas.ltfs_manifest import TapeMetadataWriter
from openblade.nas.manifest_validator import ManifestValidator
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.types import (
    CatalogRebuildRunRecord,
    DatasetStatus,
    IngestMode,
    ManifestVersionRecord,
    NasDataset,
    NasFileRecord,
    NasFileState,
    RebuildPlanRequest,
    RebuildPlanResult,
    RebuildRunStatus,
)


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


class CatalogRebuildPlanner:
    """
    Plans and executes a catalog rebuild from on-tape metadata.

    Reads /.openblade/manifest.json, catalog-shard.json, and tape.json from each tape.
    Never writes user data. Never accesses real hardware directly.
    """

    def __init__(
        self,
        repo: CatalogRepository,
        metadata_writer: TapeMetadataWriter,
        shard_writer: CatalogShardWriter,
        manifest_validator: ManifestValidator,
        path_mapping_service: PathMappingService,
    ) -> None:
        """Inject service dependencies. No mutable class-level state."""
        self.repo = repo
        self.metadata_writer = metadata_writer
        self.shard_writer = shard_writer
        self.manifest_validator = manifest_validator
        self.path_mapping_service = path_mapping_service

    def plan_rebuild(self, request: RebuildPlanRequest) -> RebuildPlanResult:
        """
        Build a rebuild plan from tape metadata and optionally persist a run record.

        For each barcode this method verifies tape.json, validates manifest.json,
        loads catalog-shard.json, and estimates recovered object counts.
        """
        barcodes_to_scan: list[str] = []
        barcodes_missing_manifest: list[str] = []
        barcodes_missing_shard: list[str] = []
        barcodes_invalid: list[str] = []
        warnings: list[str] = []
        estimated_files = 0
        estimated_datasets = 0
        estimated_path_mappings = 0

        for barcode in self._unique_barcodes(request.barcodes):
            if self.metadata_writer.read_tape_json(barcode) is None:
                warnings.append(f"{barcode}: tape metadata unavailable")
                continue
            manifest = self.metadata_writer.read_manifest(barcode)
            if manifest is None:
                # Distinguish missing from corrupt: check if the file exists in backend.
                existing_files = self.metadata_writer.list_metadata_files(
                    barcode, "/.openblade/manifest"
                )
                if "/.openblade/manifest.json" in existing_files:
                    barcodes_invalid.append(barcode)
                    warnings.append(f"{barcode}: manifest.json exists but could not be parsed")
                else:
                    barcodes_missing_manifest.append(barcode)
                    warnings.append(f"{barcode}: manifest.json missing")
                continue

            validation = self.manifest_validator.validate_manifest(barcode)
            if not validation.valid:
                barcodes_invalid.append(barcode)
                warnings.append(f"{barcode}: manifest validation failed")
                continue

            shard = self.shard_writer.read_shard(barcode)
            if shard is None:
                barcodes_missing_shard.append(barcode)
                warnings.append(f"{barcode}: catalog-shard.json missing")
                continue

            barcodes_to_scan.append(barcode)
            estimated_files += len(shard.files)
            estimated_datasets += len(shard.datasets)
            estimated_path_mappings += len(self.shard_writer.shard_to_path_mappings(shard))

        safe_to_enqueue = len(barcodes_invalid) == 0 and len(barcodes_to_scan) > 0
        run_id = ""
        if not request.dry_run:
            now = _utcnow_iso()
            run_id = str(uuid4())
            self.repo.create_rebuild_run(
                CatalogRebuildRunRecord(
                    id=run_id,
                    status=RebuildRunStatus.PLANNED,
                    triggered_by=request.triggered_by,
                    barcodes_planned=barcodes_to_scan,
                    barcodes_completed=[],
                    barcodes_failed=[],
                    barcodes_skipped=[],
                    files_recovered=0,
                    datasets_recovered=0,
                    path_mappings_recovered=0,
                    error_summary=[],
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                ).model_dump(mode="json")
            )

        return RebuildPlanResult(
            run_id=run_id,
            dry_run=request.dry_run,
            barcodes_to_scan=barcodes_to_scan,
            barcodes_missing_manifest=barcodes_missing_manifest,
            barcodes_missing_shard=barcodes_missing_shard,
            barcodes_invalid=barcodes_invalid,
            estimated_files=estimated_files,
            estimated_datasets=estimated_datasets,
            estimated_path_mappings=estimated_path_mappings,
            warnings=warnings,
            safe_to_enqueue=safe_to_enqueue,
        )

    def execute_rebuild_run(self, run_id: str) -> CatalogRebuildRunRecord:
        """
        Execute a planned rebuild run and persist progress in the catalog database.

        The run must already exist with status=planned. Successful tape recovery
        restores datasets, file records, path mappings, and manifest version rows.
        """
        stored_run = self.repo.get_rebuild_run(run_id)
        if stored_run is None:
            raise KeyError(f"Unknown rebuild run: {run_id}")

        run = CatalogRebuildRunRecord.model_validate(stored_run)
        if run.status is not RebuildRunStatus.PLANNED:
            raise ValueError("rebuild run must be in planned state")

        started_at = _utcnow_iso()
        updated = self.repo.update_rebuild_run(
            run_id,
            {
                "status": RebuildRunStatus.RUNNING.value,
                "updated_at": started_at,
                "error_summary": [],
            },
        )
        assert updated is not None
        current = CatalogRebuildRunRecord.model_validate(updated)

        completed: list[str] = []
        failed: list[str] = []
        skipped: list[str] = list(current.barcodes_skipped)
        error_summary: list[str] = []
        files_recovered = 0
        datasets_recovered = 0
        path_mappings_recovered = 0

        for barcode in current.barcodes_planned:
            try:
                shard = self.shard_writer.read_shard(barcode)
                if shard is None:
                    failed.append(barcode)
                    error_summary.append(f"catalog shard unavailable for {barcode}")
                    continue
                self.repo.add_cartridge(barcode)
                datasets_recovered += self._recover_datasets(barcode, shard)
                files_recovered += self._recover_files(barcode, shard)
                path_mappings_recovered += self._recover_path_mappings(shard)
                self._recover_manifest_versions(barcode)
                completed.append(barcode)
            except Exception:
                failed.append(barcode)
                error_summary.append(f"failed to recover catalog data for {barcode}")

        finished_at = _utcnow_iso()
        final = self.repo.update_rebuild_run(
            run_id,
            {
                "status": (
                    RebuildRunStatus.FAILED.value if failed else RebuildRunStatus.COMPLETED.value
                ),
                "barcodes_completed": completed,
                "barcodes_failed": failed,
                "barcodes_skipped": skipped,
                "files_recovered": files_recovered,
                "datasets_recovered": datasets_recovered,
                "path_mappings_recovered": path_mappings_recovered,
                "error_summary": error_summary,
                "updated_at": finished_at,
                "completed_at": finished_at,
            },
        )
        assert final is not None
        return CatalogRebuildRunRecord.model_validate(final)

    def _recover_datasets(self, barcode: str, shard: CatalogShard) -> int:
        """Upsert dataset records from shard; count only datasets not already in the catalog."""
        count = 0
        for entry in shard.datasets:
            existing = self.repo.get_nas_dataset(entry.dataset_id) or {}
            is_new = not existing
            existing_tape_set: list[str] = list(existing.get("tape_set") or [])
            tape_set = list(entry.tape_set or [barcode])
            # Merge: add this barcode and any from existing without duplicates.
            merged = list(existing_tape_set)
            for bc in tape_set:
                if bc not in merged:
                    merged.append(bc)
            dataset = NasDataset(
                id=entry.dataset_id,
                pool_id=entry.pool_id or existing.get("pool_id"),
                name=str(existing.get("name") or entry.dataset_id),
                source_path=existing.get("source_path"),
                source_host=existing.get("source_host"),
                policy_id=entry.policy or existing.get("policy_id"),
                ingest_mode=self._parse_ingest_mode(entry.ingest_mode) or existing.get("ingest_mode"),
                volume_group_id=entry.volume_group or existing.get("volume_group_id"),
                tape_set=merged,
                shard_map=dict(existing.get("shard_map") or {}),
                file_count=entry.file_count,
                total_bytes=entry.total_bytes,
                status=DatasetStatus.ARCHIVED,
                copies_completed=len(merged),
                manifest_path="/.openblade/manifest.json",
                created_at=existing.get("created_at"),
                updated_at=_utcnow_iso(),
            )
            self.repo.upsert_nas_dataset(dataset.model_dump(mode="json"))
            if is_new:
                count += 1
        return count

    def _recover_files(self, barcode: str, shard: CatalogShard) -> int:
        """Upsert file records from shard; count only files not already in the catalog."""
        count = 0
        for entry in shard.files:
            existing = self.repo.get_nas_file_record(entry.file_record_id) or {}
            is_new = not existing
            # Preserve existing tape_barcode when file already catalogued elsewhere.
            tape_barcode = existing.get("tape_barcode") or barcode
            file_record = NasFileRecord(
                id=entry.file_record_id,
                dataset_id=entry.dataset_id,
                pool_id=entry.pool_id or existing.get("pool_id"),
                relative_path=entry.logical_path,
                source_path=existing.get("source_path"),
                size_bytes=entry.size,
                mtime=entry.mtime or existing.get("mtime"),
                checksum_sha256=entry.checksum or existing.get("checksum_sha256"),
                tape_barcode=tape_barcode,
                tape_offset=existing.get("tape_offset"),
                status=NasFileState.OFFLINE_ON_TAPE,
                cache_path=None,
                created_at=existing.get("created_at"),
                updated_at=_utcnow_iso(),
            )
            self.repo.upsert_nas_file_record(file_record.model_dump(mode="json"))
            if is_new:
                count += 1
        return count

    def _recover_path_mappings(self, shard: CatalogShard) -> int:
        """Upsert path mappings, merging all_barcodes for multi-copy files; count net-new only."""
        count = 0
        for record in self.shard_writer.shard_to_path_mappings(shard):
            existing_lookup = self.path_mapping_service.lookup(record.logical_path, record.pool_id)
            if existing_lookup.found:
                # Merge barcodes from both existing and incoming records.
                merged_barcodes = list(existing_lookup.all_barcodes)
                for bc in record.all_barcodes:
                    if bc not in merged_barcodes:
                        merged_barcodes.append(bc)
                record = record.model_copy(update={"all_barcodes": merged_barcodes})
                self.path_mapping_service.record_file(record)
                # Not a new mapping — don't increment count.
            else:
                self.path_mapping_service.record_file(record)
                count += 1
        return count

    def _recover_manifest_versions(self, barcode: str) -> int:
        current_manifest = self.metadata_writer.read_manifest(barcode)
        current_checksum = ""
        if current_manifest is not None:
            current_checksum = self.metadata_writer.compute_json_checksum(
                current_manifest.model_dump(by_alias=True)
            )

        version_paths = self._select_version_paths(
            self.metadata_writer.list_metadata_files(barcode, "/.openblade/versions/manifest.")
        )
        count = 0
        for path in version_paths:
            payload = self.metadata_writer._read_json(barcode, path)
            if payload is None:
                continue
            version_ts = self._extract_version_ts(path)
            if not version_ts:
                continue
            files = payload.get("files", [])
            file_count = len(files) if isinstance(files, list) else 0
            sha256 = self.metadata_writer.compute_json_checksum(payload)
            self.repo.create_manifest_version(
                ManifestVersionRecord(
                    id=f"{barcode}:{version_ts}",
                    barcode=barcode,
                    version_ts=version_ts,
                    manifest_path=path,
                    sha256=sha256,
                    file_count=file_count,
                    is_current=bool(current_checksum and current_checksum == sha256),
                    recorded_at=_utcnow_iso(),
                ).model_dump(mode="json")
            )
            count += 1
        return count

    @staticmethod
    def _extract_version_ts(path: str) -> str:
        prefix = "/.openblade/versions/manifest."
        if not path.startswith(prefix):
            return ""
        remainder = path[len(prefix) :]
        for suffix in (".json", ".tmp"):
            if remainder.endswith(suffix):
                return remainder[: -len(suffix)]
        return ""

    @classmethod
    def _select_version_paths(cls, paths: list[str]) -> list[str]:
        selected: dict[str, str] = {}
        for path in sorted(paths):
            version_ts = cls._extract_version_ts(path)
            if not version_ts:
                continue
            current = selected.get(version_ts)
            if current is None or path.endswith(".json"):
                selected[version_ts] = path
        return [selected[key] for key in sorted(selected, reverse=True)]

    @staticmethod
    def _unique_barcodes(barcodes: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for barcode in barcodes:
            if barcode in seen:
                continue
            seen.add(barcode)
            ordered.append(barcode)
        return ordered

    @staticmethod
    def _parse_ingest_mode(value: object) -> IngestMode | None:
        if value in (None, ""):
            return None
        try:
            return IngestMode(str(value))
        except ValueError:
            return None
