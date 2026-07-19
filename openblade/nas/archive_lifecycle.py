from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from openblade.catalog.repository import CatalogRepository
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
from openblade.nas.manifest_validator import ManifestValidator, VersionedManifestWriter
from openblade.nas.path_mapping import PathMappingService
from openblade.nas.types import (
    DatasetStatus,
    NasDataset,
    NasFileRecord,
    NasFileState,
    PathMappingRecord,
)


class ArchiveLifecycleResult(BaseModel):
    """Result of completing archive lifecycle for one file."""

    file_record_id: str
    logical_path: str
    barcode: str
    success: bool
    steps_completed: list[str] = Field(default_factory=list)
    steps_failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    final_file_state: str = "offline_on_tape"


class DatasetArchiveResult(BaseModel):
    """Result of completing archive lifecycle for a full dataset."""

    dataset_id: str
    success: bool
    files_completed: int = 0
    files_failed: int = 0
    file_results: list[ArchiveLifecycleResult] = Field(default_factory=list)
    dataset_marked_archived: bool = False
    errors: list[str] = Field(default_factory=list)


class ArchiveLifecycleManager:
    """
    Orchestrates the mandatory post-write metadata completion steps for every
    tape archive operation. Must be called after file content is written to tape.

    Safety: This manager NEVER writes user data to tape. It only writes metadata
    to /.openblade/ paths via TapeMetadataWriter.
    No direct tape hardware access.
    """

    def __init__(
        self,
        repo: CatalogRepository,
        metadata_writer: TapeMetadataWriter,
        shard_writer: CatalogShardWriter,
        versioned_manifest_writer: VersionedManifestWriter,
        manifest_validator: ManifestValidator,
        path_mapping_service: PathMappingService,
    ) -> None:
        """Inject all required service dependencies. No mutable class-level state."""
        self.repo = repo
        self.metadata_writer = metadata_writer
        self.shard_writer = shard_writer
        self.versioned_manifest_writer = versioned_manifest_writer
        self.manifest_validator = manifest_validator
        self.path_mapping_service = path_mapping_service

    def complete_file_archive(
        self,
        file_record: NasFileRecord,
        barcode: str,
        tape_path: str,
        policy_name: str = "",
    ) -> ArchiveLifecycleResult:
        """
        Execute the mandatory 6-step post-write lifecycle for a single archived file.

        Steps run in strict order; failure at any step halts the sequence.
        No user data is written — only /.openblade/ metadata is touched.
        """
        result = ArchiveLifecycleResult(
            file_record_id=file_record.id,
            logical_path=file_record.relative_path,
            barcode=barcode,
            success=False,
            final_file_state=self._current_file_state(file_record.id, fallback=file_record.status),
        )
        step_calls = [
            ("verify_checksum", lambda: self._step_verify_checksum(file_record, barcode, tape_path)),
            (
                "write_manifest",
                lambda: self._step_write_manifest(file_record, barcode, tape_path, policy_name),
            ),
            (
                "write_catalog_shard",
                lambda: self._step_write_catalog_shard(
                    file_record,
                    barcode,
                    file_record.dataset_id,
                    file_record.pool_id or "",
                ),
            ),
            (
                "update_path_mapping",
                lambda: self._step_update_path_mapping(
                    file_record,
                    barcode,
                    file_record.pool_id or "",
                    policy_name,
                ),
            ),
            ("update_tape_json", lambda: self._step_update_tape_json(barcode)),
            ("mark_file_archived", lambda: self._step_mark_file_archived(file_record)),
        ]
        for step_name, step_call in step_calls:
            try:
                if not step_call():
                    result.steps_failed.append(step_name)
                    result.errors.append(f"{step_name} failed")
                    result.final_file_state = self._current_file_state(
                        file_record.id,
                        fallback=file_record.status,
                    )
                    return result
                result.steps_completed.append(step_name)
            except Exception:  # pragma: no cover - guarded by failure-path tests
                result.steps_failed.append(step_name)
                result.errors.append(f"{step_name} encountered an unexpected error")
                result.final_file_state = self._current_file_state(
                    file_record.id,
                    fallback=file_record.status,
                )
                return result
        result.success = True
        result.final_file_state = self._current_file_state(
            file_record.id,
            fallback=NasFileState.OFFLINE_ON_TAPE,
        )
        return result

    def complete_dataset_archive(
        self,
        dataset_id: str,
        file_records: list[NasFileRecord],
        barcode: str,
        tape_paths: dict[str, str],
        policy_name: str = "",
    ) -> DatasetArchiveResult:
        """
        Run complete_file_archive for every file in the dataset.

        Marks the dataset as ARCHIVED only if every file's lifecycle succeeds
        and all file records read OFFLINE_ON_TAPE from the DB (atomic guard).
        """
        result = DatasetArchiveResult(dataset_id=dataset_id, success=False)
        for file_record in file_records:
            current = self._get_file_record(file_record.id) or file_record
            resolved_barcode = current.tape_barcode or barcode
            if not resolved_barcode:
                file_result = ArchiveLifecycleResult(
                    file_record_id=current.id,
                    logical_path=current.relative_path,
                    barcode=barcode,
                    success=False,
                    steps_failed=["resolve_tape_path"],
                    errors=["missing barcode for file archive completion"],
                    final_file_state=self._current_file_state(current.id, fallback=current.status),
                )
            elif current.status is NasFileState.OFFLINE_ON_TAPE:
                file_result = ArchiveLifecycleResult(
                    file_record_id=current.id,
                    logical_path=current.relative_path,
                    barcode=resolved_barcode,
                    success=True,
                    final_file_state=NasFileState.OFFLINE_ON_TAPE.value,
                )
            else:
                tape_path = tape_paths.get(current.id)
                if not tape_path:
                    file_result = ArchiveLifecycleResult(
                        file_record_id=current.id,
                        logical_path=current.relative_path,
                        barcode=resolved_barcode,
                        success=False,
                        steps_failed=["resolve_tape_path"],
                        errors=[f"missing tape path for file {current.id}"],
                        final_file_state=self._current_file_state(current.id, fallback=current.status),
                    )
                else:
                    file_result = self.complete_file_archive(
                        current,
                        resolved_barcode,
                        tape_path,
                        policy_name,
                    )
            result.file_results.append(file_result)
            if file_result.success:
                result.files_completed += 1
            else:
                result.files_failed += 1
                result.errors.extend(file_result.errors)

        refreshed_records = [
            self._get_file_record(file_record.id) or file_record for file_record in file_records
        ]
        all_archived = bool(refreshed_records) and all(
            file_record.status is NasFileState.OFFLINE_ON_TAPE for file_record in refreshed_records
        )
        if result.files_failed == 0 and all_archived:
            dataset = self._get_dataset(dataset_id)
            if dataset is None:
                result.errors.append(f"dataset {dataset_id} not found")
            else:
                all_dataset_records = [
                    NasFileRecord.model_validate(row)
                    for row in self.repo.list_nas_file_records(dataset_id)
                ]
                tape_set = _ordered_unique(
                    [record.tape_barcode for record in all_dataset_records if record.tape_barcode]
                )
                shard_map = {
                    tape_barcode: sorted(
                        record.relative_path
                        for record in all_dataset_records
                        if record.tape_barcode == tape_barcode
                    )
                    for tape_barcode in tape_set
                }
                self.repo.upsert_nas_dataset(
                    dataset.model_copy(
                        update={
                            "status": DatasetStatus.ARCHIVED,
                            "tape_set": tape_set,
                            "shard_map": shard_map,
                            "copies_completed": len(tape_set),
                        }
                    ).model_dump(mode="json")
                )
                result.dataset_marked_archived = True
        result.success = result.files_failed == 0 and result.dataset_marked_archived
        if not result.dataset_marked_archived and result.files_failed == 0:
            result.errors.append("dataset not marked archived")
        return result

    def _step_verify_checksum(self, file_record: NasFileRecord, barcode: str, tape_path: str) -> bool:
        """Verify tape bytes exist and match file_record.checksum_sha256 when available."""
        tape_bytes = self.metadata_writer._read_bytes(barcode, tape_path)
        if tape_bytes is None:
            return False
        expected = file_record.checksum_sha256
        if expected:
            actual = hashlib.sha256(tape_bytes).hexdigest()
            return actual == expected
        return True

    def _step_write_manifest(
        self,
        file_record: NasFileRecord,
        barcode: str,
        tape_path: str,
        policy_name: str,
    ) -> bool:
        dataset = self._require_dataset(file_record.dataset_id)
        existing = self.metadata_writer.read_manifest(barcode)
        files = [] if existing is None else [
            entry for entry in existing.files if entry.file_record_id != file_record.id
        ]
        files.append(
            ManifestFileEntry(
                logical_path=file_record.relative_path,
                tape_path=tape_path,
                dataset_id=file_record.dataset_id,
                file_record_id=file_record.id,
                size=file_record.size_bytes,
                mtime=file_record.mtime or "",
                checksum=file_record.checksum_sha256 or "",
                policy=policy_name,
                verified=True,
            )
        )
        files.sort(key=lambda entry: (entry.logical_path, entry.file_record_id))
        manifest = ManifestJson(
            barcode=barcode,
            openblade_tape_id=existing.openblade_tape_id if existing is not None else barcode,
            volume_group=(existing.volume_group if existing is not None else dataset.volume_group_id) or "",
            pools=_ordered_unique([
                *(existing.pools if existing is not None else []),
                *([file_record.pool_id] if file_record.pool_id else []),
            ]),
            datasets=_ordered_unique([
                *(existing.datasets if existing is not None else []),
                file_record.dataset_id,
            ]),
            tape_sets=list(existing.tape_sets if existing is not None else []),
            shard_sets=list(existing.shard_sets if existing is not None else []),
            files=files,
        )
        temp_path = self.versioned_manifest_writer.begin_write(barcode, manifest)
        self.versioned_manifest_writer.commit_write(barcode, temp_path)
        return self.manifest_validator.validate_manifest(barcode).valid

    def _step_write_catalog_shard(
        self,
        file_record: NasFileRecord,
        barcode: str,
        dataset_id: str,
        pool_id: str,
    ) -> bool:
        dataset = self._require_dataset(dataset_id)
        existing = self.shard_writer.read_shard(barcode)
        files = [] if existing is None else [
            entry for entry in existing.files if entry.file_record_id != file_record.id
        ]
        files.append(
            CatalogShardFileEntry(
                logical_path=file_record.relative_path,
                tape_path=self._resolve_tape_path(barcode, file_record.id),
                dataset_id=dataset_id,
                file_record_id=file_record.id,
                pool_id=pool_id,
                size=file_record.size_bytes,
                mtime=file_record.mtime or "",
                checksum=file_record.checksum_sha256 or "",
                file_state=NasFileState.OFFLINE_ON_TAPE.value,
                policy=dataset.policy_id or "",
                verified=True,
            )
        )
        files.sort(key=lambda entry: (entry.logical_path, entry.file_record_id))
        dataset_files = [entry for entry in files if entry.dataset_id == dataset_id]
        datasets = [] if existing is None else [
            entry for entry in existing.datasets if entry.dataset_id != dataset_id
        ]
        datasets.append(
            CatalogShardDatasetEntry(
                dataset_id=dataset_id,
                pool_id=pool_id,
                volume_group=dataset.volume_group_id or "",
                policy=dataset.policy_id or "",
                ingest_mode=(dataset.ingest_mode.value if dataset.ingest_mode is not None else ""),
                file_count=len(dataset_files),
                total_bytes=sum(entry.size for entry in dataset_files),
                tape_set=[barcode],
                shard_set=[],
            )
        )
        shard = CatalogShard(
            barcode=barcode,
            openblade_tape_id=existing.openblade_tape_id if existing is not None else barcode,
            volume_group=(existing.volume_group if existing is not None else dataset.volume_group_id) or "",
            generated_at=_utcnow_iso(),
            datasets=sorted(datasets, key=lambda entry: entry.dataset_id),
            files=files,
        )
        self.shard_writer.write_shard(barcode, shard)
        return self.manifest_validator.validate_catalog_shard(barcode).valid

    def _step_update_path_mapping(
        self,
        file_record: NasFileRecord,
        barcode: str,
        pool_id: str,
        policy_name: str,
    ) -> bool:
        del policy_name
        record = self.path_mapping_service.record_file(
            PathMappingRecord(
                logical_path=file_record.relative_path,
                pool_id=pool_id,
                dataset_id=file_record.dataset_id,
                primary_barcode=barcode,
                all_barcodes=[barcode],
                file_record_id=file_record.id,
                file_state=NasFileState.OFFLINE_ON_TAPE,
                restore_strategy="single_tape",
                size=file_record.size_bytes,
                checksum=file_record.checksum_sha256 or "",
                last_seen_at=_utcnow_iso(),
            )
        )
        return self.path_mapping_service.lookup(record.logical_path, record.pool_id).found

    def _step_update_tape_json(self, barcode: str) -> bool:
        existing = self.metadata_writer.read_tape_json(barcode)
        created_at = existing.created_at if existing is not None else _utcnow_iso()
        cartridge = self.repo.get_cartridge(barcode)
        tape_json = TapeJson(
            openblade_tape_id=existing.openblade_tape_id if existing is not None else barcode,
            barcode=barcode,
            ltfs_volume_uuid=existing.ltfs_volume_uuid if existing is not None else "",
            library_type=existing.library_type if existing is not None else "Quantum Scalar i3",
            created_at=created_at,
            last_openblade_write_at=_utcnow_iso(),
            openblade_version=existing.openblade_version if existing is not None else "0.1.0",
            volume_group=(
                existing.volume_group
                if existing is not None
                else (cartridge.volume_group_id if cartridge is not None else "")
            )
            or "",
            pools=list(existing.pools if existing is not None else []),
            state=existing.state if existing is not None else "active",
            generation=existing.generation if existing is not None else "LTO-8",
            notes=existing.notes if existing is not None else "",
        )
        self.metadata_writer.write_tape_json(barcode, tape_json)
        return self.metadata_writer.read_tape_json(barcode) is not None

    def _step_mark_file_archived(self, file_record: NasFileRecord) -> bool:
        return self.repo.update_nas_file_status(file_record.id, NasFileState.OFFLINE_ON_TAPE)

    def _get_dataset(self, dataset_id: str) -> NasDataset | None:
        payload = self.repo.get_nas_dataset(dataset_id)
        if payload is None:
            return None
        return NasDataset.model_validate(payload)

    def _require_dataset(self, dataset_id: str) -> NasDataset:
        dataset = self._get_dataset(dataset_id)
        if dataset is None:
            raise ValueError(f"dataset {dataset_id} not found")
        return dataset

    def _get_file_record(self, file_id: str) -> NasFileRecord | None:
        payload = self.repo.get_nas_file_record(file_id)
        if payload is None:
            return None
        return NasFileRecord.model_validate(payload)

    def _current_file_state(self, file_id: str, fallback: NasFileState) -> str:
        current = self._get_file_record(file_id)
        if current is None:
            return fallback.value
        return current.status.value

    def _resolve_tape_path(self, barcode: str, file_record_id: str) -> str:
        manifest = self.metadata_writer.read_manifest(barcode)
        if manifest is not None:
            for entry in manifest.files:
                if entry.file_record_id == file_record_id:
                    return entry.tape_path
        return ""


def _ordered_unique(values: list[str | None]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
