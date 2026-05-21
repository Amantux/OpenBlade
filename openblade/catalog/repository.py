"""SQLAlchemy-backed catalog repository."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from openblade.catalog.models import (
    Cartridge,
    CatalogRebuildRun,
    FileInstance,
    FileRecord,
    Job,
    ManifestVersion,
    NasCacheDrive,
    NasConfig,
    NasDataset,
    NasFileRecord,
    NasPool,
    NasRestoreJob,
    NasShare,
    NasStoragePolicy,
    PathMapping,
    SafetyTokenRecord,
    VolumeGroup,
)
from openblade.domain.errors import FileNotFoundError
from openblade.domain.models import FileInstanceState
from openblade.domain.policies import SafetyToken
from openblade.nas.types import (
    CacheDriveConfig,
    CatalogRebuildRunRecord,
    ManifestVersionRecord,
    NasShareDefinition,
    PathMappingRecord,
    PathMappingSearchRequest,
    RestoreJobStatus,
    StoragePolicy,
)
from openblade.nas.types import (
    NasDataset as NasDatasetModel,
)
from openblade.nas.types import (
    NasFileRecord as NasFileRecordModel,
)
from openblade.nas.types import (
    NasPool as NasPoolModel,
)
from openblade.nas.types import (
    NasRestoreJob as NasRestoreJobModel,
)

_ARCHIVED_INSTANCE_STATES = {
    FileInstanceState.ARCHIVED.value,
    FileInstanceState.VERIFIED.value,
}


def _model_to_json_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return model.dict()


def _load_json_value(value: str | None, default: object) -> object:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


@dataclass(frozen=True)
class CatalogBrowseEntry:
    path: str
    size: int
    tape_barcode: str
    archived_at: datetime | None
    shard_count: int


class CatalogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._lock = threading.RLock()

    def _validate_nas_config_data(self, data: object, *, entity_name: str) -> dict[str, object]:
        try:
            value = json.loads(json.dumps(data))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{entity_name} config_json must be a valid JSON object") from exc
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{entity_name} config_json must be a non-empty JSON object")
        return value

    def _nas_pool_to_dict(self, row: NasPool) -> dict[str, object]:
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "volume_group_ids": _load_json_value(row.volume_group_ids, []),
            "default_policy_id": row.default_policy_id,
            "default_ingest_mode": row.default_ingest_mode,
            "mount_path": row.mount_path,
            "virtual_mount_enabled": row.virtual_mount_enabled,
            "hydration_behavior": row.hydration_behavior,
            "cache_target_id": row.cache_target_id,
            "restore_target_path": row.restore_target_path,
            "access_mode": row.access_mode,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _nas_dataset_to_dict(self, row: NasDataset) -> dict[str, object]:
        return {
            "id": row.id,
            "pool_id": row.pool_id,
            "name": row.name,
            "source_path": row.source_path,
            "source_host": row.source_host,
            "policy_id": row.policy_id,
            "ingest_mode": row.ingest_mode,
            "volume_group_id": row.volume_group_id,
            "tape_set": _load_json_value(row.tape_set, []),
            "shard_map": _load_json_value(row.shard_map, {}),
            "file_count": row.file_count,
            "total_bytes": row.total_bytes,
            "status": row.status,
            "copies_completed": row.copies_completed,
            "manifest_path": row.manifest_path,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _nas_file_record_to_dict(self, row: NasFileRecord) -> dict[str, object]:
        return {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "pool_id": row.pool_id,
            "relative_path": row.relative_path,
            "source_path": row.source_path,
            "size_bytes": row.size_bytes,
            "mtime": row.mtime,
            "checksum_sha256": row.checksum_sha256,
            "tape_barcode": row.tape_barcode,
            "tape_offset": row.tape_offset,
            "status": row.status,
            "cache_path": row.cache_path,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _nas_restore_job_to_dict(self, row: NasRestoreJob) -> dict[str, object]:
        return {
            "id": row.id,
            "status": row.status,
            "priority": row.priority,
            "paths": _load_json_value(row.paths, []),
            "pool_id": row.pool_id,
            "dataset_id": row.dataset_id,
            "destination": row.destination,
            "allow_parallel": row.allow_parallel,
            "max_drives": row.max_drives,
            "cache_policy": row.cache_policy,
            "overwrite_policy": row.overwrite_policy,
            "required_tapes": _load_json_value(row.required_tapes, []),
            "missing_tapes": _load_json_value(row.missing_tapes, []),
            "exported_tapes": _load_json_value(row.exported_tapes, []),
            "tape_load_order": _load_json_value(row.tape_load_order, []),
            "parallel_restore_groups": _load_json_value(row.parallel_restore_groups, {}),
            "estimated_bytes": row.estimated_bytes,
            "bytes_restored": row.bytes_restored,
            "files_restored": row.files_restored,
            "files_failed": row.files_failed,
            "partial_success": row.partial_success,
            "unavailable_files": _load_json_value(row.unavailable_files, []),
            "warnings": _load_json_value(row.warnings, []),
            "error_message": row.error_message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
        }

    def _path_mapping_to_dict(self, row: PathMapping) -> dict[str, object]:
        return {
            "id": row.id,
            "logical_path": row.logical_path,
            "pool_id": row.pool_id or "",
            "dataset_id": row.dataset_id or "",
            "primary_barcode": row.primary_barcode or "",
            "all_barcodes": _load_json_value(row.all_barcodes, []),
            "file_record_id": row.file_record_id or "",
            "file_state": row.file_state,
            "restore_strategy": row.restore_strategy,
            "size": row.size or 0,
            "checksum": row.checksum or "",
            "last_seen_at": row.last_seen_at or "",
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    def _catalog_rebuild_run_to_dict(self, row: CatalogRebuildRun) -> dict[str, object]:
        return {
            "id": row.id,
            "status": row.status,
            "triggered_by": row.triggered_by,
            "barcodes_planned": _load_json_value(row.barcodes_planned, []),
            "barcodes_completed": _load_json_value(row.barcodes_completed, []),
            "barcodes_failed": _load_json_value(row.barcodes_failed, []),
            "barcodes_skipped": _load_json_value(row.barcodes_skipped, []),
            "files_recovered": row.files_recovered,
            "datasets_recovered": row.datasets_recovered,
            "path_mappings_recovered": row.path_mappings_recovered,
            "error_summary": _load_json_value(row.error_summary, []),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
        }

    def _manifest_version_to_dict(self, row: ManifestVersion) -> dict[str, object]:
        return {
            "id": row.id,
            "barcode": row.barcode,
            "version_ts": row.version_ts,
            "manifest_path": row.manifest_path,
            "sha256": row.sha256,
            "file_count": row.file_count,
            "is_current": row.is_current,
            "recorded_at": row.recorded_at,
        }

    def __getattribute__(self, name: str):
        attr = object.__getattribute__(self, name)
        if name.startswith("_") or name == "session" or not callable(attr):
            return attr

        def _locked(*args, **kwargs):
            with object.__getattribute__(self, "_lock"):
                return attr(*args, **kwargs)

        return _locked

    def create_volume_group(self, name: str) -> VolumeGroup:
        existing = self.get_volume_group(name)
        if existing is not None:
            return existing
        group = VolumeGroup(name=name)
        self.session.add(group)
        self.session.commit()
        self.session.refresh(group)
        return group

    def get_volume_group(self, name: str) -> VolumeGroup | None:
        stmt = (
            select(VolumeGroup)
            .options(selectinload(VolumeGroup.cartridges))
            .where(VolumeGroup.name == name)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_volume_groups(self) -> list[VolumeGroup]:
        stmt = (
            select(VolumeGroup)
            .options(selectinload(VolumeGroup.cartridges))
            .order_by(VolumeGroup.name)
        )
        return list(self.session.execute(stmt).scalars().all())

    def add_cartridge(self, barcode: str, volume_group_id: str | None = None) -> Cartridge:
        cartridge = self.get_cartridge(barcode)
        if cartridge is None:
            cartridge = Cartridge(barcode=barcode, volume_group_id=volume_group_id)
            self.session.add(cartridge)
        elif volume_group_id is not None:
            cartridge.volume_group_id = volume_group_id
        self.session.commit()
        self.session.refresh(cartridge)
        return cartridge

    def get_cartridge(self, barcode: str) -> Cartridge | None:
        stmt = select(Cartridge).where(Cartridge.barcode == barcode)
        return self.session.execute(stmt).scalar_one_or_none()

    def list_cartridges(self) -> list[Cartridge]:
        stmt = select(Cartridge).order_by(Cartridge.barcode)
        return list(self.session.execute(stmt).scalars().all())

    def create_file_record(
        self,
        path: str,
        size_bytes: int,
        checksum: str,
        vg_id: str,
        *,
        shard_count: int | None = None,
        shard_index: int | None = None,
        block_size: int | None = None,
        shard_profile: str | None = None,
        parent_id: str | None = None,
    ) -> FileRecord:
        normalized = str(PurePosixPath(path))
        record = self.get_file_record(normalized)
        if record is None:
            record = FileRecord(
                path=normalized,
                size_bytes=size_bytes,
                checksum_sha256=checksum,
                volume_group_id=vg_id,
                shard_count=shard_count,
                shard_index=shard_index,
                block_size=block_size,
                shard_profile=shard_profile,
                parent_id=parent_id,
            )
            self.session.add(record)
        else:
            record.size_bytes = size_bytes
            record.checksum_sha256 = checksum
            record.volume_group_id = vg_id
            record.shard_count = shard_count
            record.shard_index = shard_index
            record.block_size = block_size
            record.shard_profile = shard_profile
            record.parent_id = parent_id
        self.session.commit()
        self.session.refresh(record)
        return record

    def get_file_record(self, path: str) -> FileRecord | None:
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.path == str(PurePosixPath(path)))
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_file_records(self, path_prefix: str = "/") -> list[FileRecord]:
        prefix = str(PurePosixPath(path_prefix))
        like_prefix = "%" if prefix == "/" else f"{prefix}%"
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.parent_id.is_(None), FileRecord.path.like(like_prefix))
            .order_by(FileRecord.path)
        )
        return list(self.session.execute(stmt).scalars().all())

    def list_catalog_files(
        self, limit: int = 50, offset: int = 0, search: str | None = None
    ) -> tuple[list[FileRecord], int]:
        stmt = select(FileRecord).options(selectinload(FileRecord.instances)).where(FileRecord.parent_id.is_(None))
        count_stmt = select(func.count()).select_from(FileRecord).where(FileRecord.parent_id.is_(None))
        if search:
            pattern = f"%{search.strip()}%"
            stmt = stmt.where(FileRecord.path.ilike(pattern))
            count_stmt = count_stmt.where(FileRecord.path.ilike(pattern))
        stmt = stmt.order_by(FileRecord.created_at.desc(), FileRecord.path).offset(offset).limit(limit)
        records = list(self.session.execute(stmt).scalars().all())
        total = int(self.session.execute(count_stmt).scalar_one())
        return records, total

    def list_ltfs_entries(
        self, tape_barcode: str | None = None, path_prefix: str = "/"
    ) -> list[CatalogBrowseEntry]:
        entries: list[CatalogBrowseEntry] = []
        for record in self.list_file_records(path_prefix):
            matching_instances = [
                instance
                for instance in record.instances
                if instance.state in _ARCHIVED_INSTANCE_STATES
                and (tape_barcode is None or instance.barcode == tape_barcode)
            ]
            if not matching_instances:
                continue
            latest_instance = max(
                matching_instances,
                key=lambda instance: instance.archived_at or instance.created_at,
            )
            entries.append(
                CatalogBrowseEntry(
                    path=record.path,
                    size=record.size_bytes,
                    tape_barcode=latest_instance.barcode,
                    archived_at=latest_instance.archived_at,
                    shard_count=record.shard_count or len(matching_instances),
                )
            )
        return entries

    def list_catalog_tape_barcodes(self) -> list[str]:
        stmt = (
            select(FileInstance.barcode)
            .where(FileInstance.state.in_(_ARCHIVED_INSTANCE_STATES))
            .distinct()
            .order_by(FileInstance.barcode)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_file_record_by_id(self, file_id: str) -> FileRecord | None:
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.id == file_id)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def list_shard_records(self, parent_id: str) -> list[FileRecord]:
        stmt = (
            select(FileRecord)
            .options(selectinload(FileRecord.instances))
            .where(FileRecord.parent_id == parent_id)
            .order_by(FileRecord.shard_index, FileRecord.created_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def delete_file_record(self, file_id: str) -> None:
        record = self.get_file_record_by_id(file_id)
        if record is None:
            raise FileNotFoundError(f"Catalog file {file_id} not found")
        for shard_record in self.list_shard_records(file_id):
            self.session.delete(shard_record)
        self.session.delete(record)
        self.session.commit()

    def create_file_instance(
        self, file_record_id: str, barcode: str, tape_path: str
    ) -> FileInstance:
        instance = FileInstance(
            file_record_id=file_record_id,
            barcode=barcode,
            tape_path=str(PurePosixPath(tape_path)),
            state=FileInstanceState.PENDING.value,
        )
        self.session.add(instance)
        self.session.commit()
        self.session.refresh(instance)
        return instance

    def mark_instance_archived(self, instance_id: str, checksum_verified: bool = True) -> None:
        instance = self.session.get(FileInstance, instance_id)
        if instance is None:
            raise FileNotFoundError(f"File instance {instance_id} not found")
        instance.state = FileInstanceState.ARCHIVED.value
        instance.archived_at = datetime.utcnow()
        instance.checksum_verified = checksum_verified
        self.session.commit()

    def mark_instance_failed(self, instance_id: str, error: str) -> None:
        del error
        instance = self.session.get(FileInstance, instance_id)
        if instance is None:
            raise FileNotFoundError(f"File instance {instance_id} not found")
        instance.state = FileInstanceState.FAILED.value
        self.session.commit()

    def create_job(self, job_type: str, metadata: dict[str, object]) -> Job:
        job = Job(job_type=job_type, state="pending", metadata_json=json.dumps(metadata))
        self.session.add(job)
        self.session.commit()
        self.session.refresh(job)
        return job

    def update_job_state(self, job_id: str, state: str, error: str | None = None) -> None:
        job = self.session.get(Job, job_id)
        if job is None:
            raise FileNotFoundError(f"Job {job_id} not found")
        job.state = state
        job.error = error
        job.updated_at = datetime.utcnow()
        self.session.commit()

    def get_job(self, job_id: str) -> Job | None:
        return self.session.get(Job, job_id)

    def list_jobs(self, state: str | None = None) -> list[Job]:
        stmt = select(Job).order_by(Job.created_at.desc())
        if state is not None:
            stmt = stmt.where(Job.state == state)
        return list(self.session.execute(stmt).scalars().all())

    def add_barcode_to_volume_group(self, volume_group_id: str, barcode: str) -> Cartridge:
        return self.add_cartridge(barcode, volume_group_id)

    def save_file_record(
        self,
        record: object,
        barcode: str,
        tape_path: PurePosixPath,
        state: FileInstanceState,
    ) -> FileInstance:
        file_record = self.create_file_record(
            path=str(record.path),
            size_bytes=record.size_bytes,
            checksum=record.checksum_sha256,
            vg_id=record.volume_group_id,
            shard_count=getattr(record, "shard_count", None),
            shard_index=getattr(record, "shard_index", None),
            block_size=getattr(record, "block_size", None),
            shard_profile=getattr(record, "shard_profile", None),
            parent_id=getattr(record, "parent_id", None),
        )
        instance = self.create_file_instance(file_record.id, barcode, str(tape_path))
        if state in {FileInstanceState.ARCHIVED, FileInstanceState.VERIFIED}:
            self.mark_instance_archived(
                instance.id, checksum_verified=state is FileInstanceState.VERIFIED
            )
            self.session.refresh(instance)
        return instance

    def get_file(self, catalog_path: str) -> FileRecord:
        record = self.get_file_record(catalog_path)
        if record is None:
            raise FileNotFoundError(f"Catalog path {catalog_path} not found")
        return record

    def list_files(self, prefix: str = "/") -> list[FileRecord]:
        return self.list_file_records(prefix)

    def get_latest_instance_for_path(self, path: str) -> tuple[FileRecord, FileInstance]:
        record = self.get_file_record(path)
        if record is None:
            raise FileNotFoundError(f"Catalog path {path} not found")
        archived = sorted(
            [
                instance
                for instance in record.instances
                if instance.state
                in {FileInstanceState.ARCHIVED.value, FileInstanceState.VERIFIED.value}
            ],
            key=lambda instance: instance.created_at,
        )
        if not archived:
            raise FileNotFoundError(f"No archived instance for {path}")
        return record, archived[-1]

    def list_instances_for_barcode(self, barcode: str) -> list[FileInstance]:
        stmt = (
            select(FileInstance)
            .where(FileInstance.barcode == barcode)
            .order_by(FileInstance.created_at)
        )
        return list(self.session.execute(stmt).scalars().all())

    def delete_file_record_if_unarchived(self, path: str) -> None:
        record = self.get_file_record(path)
        if record is None:
            return
        if any(
            instance.state in {FileInstanceState.ARCHIVED.value, FileInstanceState.VERIFIED.value}
            for instance in record.instances
        ):
            return
        self.session.delete(record)
        self.session.commit()

    def list_nas_policies(self) -> list[NasStoragePolicy]:
        stmt = select(NasStoragePolicy).order_by(NasStoragePolicy.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_nas_policy(self, policy_id: str) -> NasStoragePolicy | None:
        return self.session.get(NasStoragePolicy, policy_id)

    def upsert_nas_policy(self, policy_id: str, data: dict[str, object]) -> NasStoragePolicy:
        data = self._validate_nas_config_data(data, entity_name="NAS policy")
        parsed = StoragePolicy.model_validate({**data, "id": policy_id})
        payload = _model_to_json_dict(parsed)
        policy = self.session.get(NasStoragePolicy, policy_id)
        if policy is None:
            policy = NasStoragePolicy(id=policy_id)
            self.session.add(policy)
        policy.name = parsed.name
        policy.policy_type = parsed.policy_type
        policy.config_json = json.dumps(payload)
        policy.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(policy)
        return policy

    def delete_nas_policy(self, policy_id: str) -> bool:
        policy = self.session.get(NasStoragePolicy, policy_id)
        if policy is None:
            return False
        self.session.delete(policy)
        self.session.commit()
        return True

    def list_nas_cache_drives(self) -> list[NasCacheDrive]:
        stmt = select(NasCacheDrive).order_by(NasCacheDrive.id)
        return list(self.session.execute(stmt).scalars().all())

    def get_nas_cache_drive(self, drive_id: str) -> NasCacheDrive | None:
        return self.session.get(NasCacheDrive, drive_id)

    def upsert_nas_cache_drive(self, drive_id: str, data: dict[str, object]) -> NasCacheDrive:
        data = self._validate_nas_config_data(data, entity_name="NAS cache drive")
        parsed = CacheDriveConfig.model_validate({**data, "id": drive_id})
        payload = _model_to_json_dict(parsed)
        drive = self.session.get(NasCacheDrive, drive_id)
        if drive is None:
            drive = NasCacheDrive(id=drive_id)
            self.session.add(drive)
        drive.name = parsed.name
        drive.root_path = parsed.root_path
        drive.config_json = json.dumps(payload)
        drive.enabled = parsed.enabled
        self.session.commit()
        self.session.refresh(drive)
        return drive

    def delete_nas_cache_drive(self, drive_id: str) -> bool:
        drive = self.session.get(NasCacheDrive, drive_id)
        if drive is None:
            return False
        self.session.delete(drive)
        self.session.commit()
        return True

    def get_nas_config(self, key: str) -> dict[str, object] | None:
        config = self.session.get(NasConfig, key)
        if config is None:
            return None
        value = json.loads(config.value_json or "{}")
        if isinstance(value, dict):
            return value
        return {"value": value}

    def set_nas_config(self, key: str, value: dict[str, object]) -> dict[str, object]:
        config = self.session.get(NasConfig, key)
        if config is None:
            config = NasConfig(key=key)
            self.session.add(config)
        config.value_json = json.dumps(value)
        self.session.commit()
        return value

    def delete_nas_config(self, key: str) -> bool:
        config = self.session.get(NasConfig, key)
        if config is None:
            return False
        self.session.delete(config)
        self.session.commit()
        return True

    def list_nas_shares(self) -> list[NasShare]:
        stmt = select(NasShare).order_by(NasShare.path)
        return list(self.session.execute(stmt).scalars().all())

    def get_nas_share(self, path: str) -> NasShare | None:
        return self.session.get(NasShare, path)

    def upsert_nas_share(self, path: str, data: dict[str, object]) -> NasShare:
        data = self._validate_nas_config_data(data, entity_name="NAS share")
        parsed = NasShareDefinition.model_validate({**data, "path": path})
        payload = _model_to_json_dict(parsed)
        share = self.session.get(NasShare, path)
        if share is None:
            share = NasShare(path=path)
            self.session.add(share)
        share.name = parsed.name
        share.share_type = parsed.share_type
        share.config_json = json.dumps(payload)
        self.session.commit()
        self.session.refresh(share)
        return share

    def delete_nas_share(self, path: str) -> bool:
        share = self.session.get(NasShare, path)
        if share is None:
            return False
        self.session.delete(share)
        self.session.commit()
        return True

    def list_nas_pools(self) -> list[dict[str, object]]:
        stmt = select(NasPool).order_by(NasPool.name)
        return [self._nas_pool_to_dict(row) for row in self.session.execute(stmt).scalars().all()]

    def get_nas_pool(self, pool_id: str) -> dict[str, object] | None:
        row = self.session.get(NasPool, pool_id)
        if row is None:
            return None
        return self._nas_pool_to_dict(row)

    def upsert_nas_pool(self, config: dict[str, object]) -> dict[str, object]:
        parsed = NasPoolModel.model_validate(config)
        row = self.session.get(NasPool, parsed.id)
        if row is None:
            row = NasPool(id=parsed.id, created_at=parsed.created_at or _utcnow_iso())
            self.session.add(row)
        row.name = parsed.name
        row.description = parsed.description
        row.volume_group_ids = json.dumps(parsed.volume_group_ids)
        row.default_policy_id = parsed.default_policy_id
        row.default_ingest_mode = parsed.default_ingest_mode
        row.mount_path = parsed.mount_path
        row.virtual_mount_enabled = parsed.virtual_mount_enabled
        row.hydration_behavior = parsed.hydration_behavior
        row.cache_target_id = parsed.cache_target_id
        row.restore_target_path = parsed.restore_target_path
        row.access_mode = parsed.access_mode
        row.created_at = row.created_at or parsed.created_at or _utcnow_iso()
        row.updated_at = parsed.updated_at or _utcnow_iso()
        self.session.commit()
        self.session.refresh(row)
        return self._nas_pool_to_dict(row)

    def delete_nas_pool(self, pool_id: str) -> bool:
        row = self.session.get(NasPool, pool_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def list_nas_datasets(self, pool_id: str | None = None) -> list[dict[str, object]]:
        stmt = select(NasDataset).order_by(NasDataset.created_at, NasDataset.name)
        if pool_id is not None:
            stmt = stmt.where(NasDataset.pool_id == pool_id)
        return [self._nas_dataset_to_dict(row) for row in self.session.execute(stmt).scalars().all()]

    def get_nas_dataset(self, dataset_id: str) -> dict[str, object] | None:
        row = self.session.get(NasDataset, dataset_id)
        if row is None:
            return None
        return self._nas_dataset_to_dict(row)

    def upsert_nas_dataset(self, config: dict[str, object]) -> dict[str, object]:
        parsed = NasDatasetModel.model_validate(config)
        row = self.session.get(NasDataset, parsed.id)
        if row is None:
            row = NasDataset(id=parsed.id, created_at=parsed.created_at or _utcnow_iso())
            self.session.add(row)
        row.pool_id = parsed.pool_id
        row.name = parsed.name
        row.source_path = parsed.source_path
        row.source_host = parsed.source_host
        row.policy_id = parsed.policy_id
        row.ingest_mode = parsed.ingest_mode
        row.volume_group_id = parsed.volume_group_id
        row.tape_set = json.dumps(parsed.tape_set)
        row.shard_map = json.dumps(parsed.shard_map)
        row.file_count = parsed.file_count
        row.total_bytes = parsed.total_bytes
        row.status = parsed.status
        row.copies_completed = parsed.copies_completed
        row.manifest_path = parsed.manifest_path
        row.created_at = row.created_at or parsed.created_at or _utcnow_iso()
        row.updated_at = parsed.updated_at or _utcnow_iso()
        self.session.commit()
        self.session.refresh(row)
        return self._nas_dataset_to_dict(row)

    def delete_nas_dataset(self, dataset_id: str) -> bool:
        row = self.session.get(NasDataset, dataset_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def list_nas_file_records(self, dataset_id: str) -> list[dict[str, object]]:
        stmt = (
            select(NasFileRecord)
            .where(NasFileRecord.dataset_id == dataset_id)
            .order_by(NasFileRecord.relative_path)
        )
        return [self._nas_file_record_to_dict(row) for row in self.session.execute(stmt).scalars().all()]

    def get_nas_file_record(self, file_id: str) -> dict[str, object] | None:
        row = self.session.get(NasFileRecord, file_id)
        if row is None:
            return None
        return self._nas_file_record_to_dict(row)

    def upsert_nas_file_record(self, config: dict[str, object]) -> dict[str, object]:
        parsed = NasFileRecordModel.model_validate(config)
        row = self.session.get(NasFileRecord, parsed.id)
        if row is None:
            row = NasFileRecord(id=parsed.id, created_at=parsed.created_at or _utcnow_iso())
            self.session.add(row)
        row.dataset_id = parsed.dataset_id
        row.pool_id = parsed.pool_id
        row.relative_path = parsed.relative_path
        row.source_path = parsed.source_path
        row.size_bytes = parsed.size_bytes
        row.mtime = parsed.mtime
        row.checksum_sha256 = parsed.checksum_sha256
        row.tape_barcode = parsed.tape_barcode
        row.tape_offset = parsed.tape_offset
        row.status = parsed.status.value
        row.cache_path = parsed.cache_path
        row.created_at = row.created_at or parsed.created_at or _utcnow_iso()
        row.updated_at = parsed.updated_at or _utcnow_iso()
        self.session.commit()
        self.session.refresh(row)
        return self._nas_file_record_to_dict(row)

    def update_nas_file_status(self, file_id: str, status: str) -> bool:
        row = self.session.get(NasFileRecord, file_id)
        if row is None:
            return False
        parsed = NasFileRecordModel.model_validate({**self._nas_file_record_to_dict(row), "status": status})
        row.status = parsed.status.value
        row.updated_at = _utcnow_iso()
        self.session.commit()
        return True

    def list_nas_restore_jobs(self, status: str | None = None) -> list[dict[str, object]]:
        stmt = select(NasRestoreJob).order_by(NasRestoreJob.created_at.desc())
        if status is not None:
            stmt = stmt.where(NasRestoreJob.status == status)
        return [self._nas_restore_job_to_dict(row) for row in self.session.execute(stmt).scalars().all()]

    def get_nas_restore_job(self, job_id: str) -> dict[str, object] | None:
        row = self.session.get(NasRestoreJob, job_id)
        if row is None:
            return None
        return self._nas_restore_job_to_dict(row)

    def upsert_nas_restore_job(self, config: dict[str, object]) -> dict[str, object]:
        parsed = NasRestoreJobModel.model_validate(config)
        row = self.session.get(NasRestoreJob, parsed.id)
        if row is None:
            row = NasRestoreJob(id=parsed.id, created_at=parsed.created_at or _utcnow_iso())
            self.session.add(row)
        row.status = parsed.status.value
        row.priority = parsed.priority
        row.paths = json.dumps(parsed.paths)
        row.pool_id = parsed.pool_id
        row.dataset_id = parsed.dataset_id
        row.destination = parsed.destination
        row.allow_parallel = parsed.allow_parallel
        row.max_drives = parsed.max_drives
        row.cache_policy = parsed.cache_policy
        row.overwrite_policy = parsed.overwrite_policy
        row.required_tapes = json.dumps(parsed.required_tapes)
        row.missing_tapes = json.dumps(parsed.missing_tapes)
        row.exported_tapes = json.dumps(parsed.exported_tapes)
        row.tape_load_order = json.dumps(parsed.tape_load_order)
        row.parallel_restore_groups = json.dumps(parsed.parallel_restore_groups)
        row.estimated_bytes = parsed.estimated_bytes
        row.bytes_restored = parsed.bytes_restored
        row.files_restored = parsed.files_restored
        row.files_failed = parsed.files_failed
        row.partial_success = parsed.partial_success
        row.unavailable_files = json.dumps(parsed.unavailable_files)
        row.warnings = json.dumps(parsed.warnings)
        row.error_message = parsed.error_message
        row.created_at = row.created_at or parsed.created_at or _utcnow_iso()
        row.updated_at = parsed.updated_at or _utcnow_iso()
        row.completed_at = parsed.completed_at
        self.session.commit()
        self.session.refresh(row)
        return self._nas_restore_job_to_dict(row)

    def update_nas_restore_job_status(
        self,
        job_id: str,
        status: str,
        *,
        bytes_restored: int | None = None,
        files_restored: int | None = None,
        files_failed: int | None = None,
        partial_success: bool | None = None,
        error_message: str | None = None,
    ) -> bool:
        row = self.session.get(NasRestoreJob, job_id)
        if row is None:
            return False

        row.status = RestoreJobStatus(status).value
        row.updated_at = _utcnow_iso()

        if bytes_restored is not None:
            row.bytes_restored = bytes_restored
        if files_restored is not None:
            row.files_restored = files_restored
        if files_failed is not None:
            row.files_failed = files_failed
        if partial_success is not None:
            row.partial_success = partial_success
        if error_message is not None:
            row.error_message = error_message
        if row.status in {
            RestoreJobStatus.COMPLETED.value,
            RestoreJobStatus.FAILED.value,
            RestoreJobStatus.CANCELLED.value,
        }:
            row.completed_at = _utcnow_iso()
        else:
            row.completed_at = None

        self.session.commit()
        return True

    def delete_nas_restore_job(self, job_id: str) -> bool:
        row = self.session.get(NasRestoreJob, job_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def create_rebuild_run(self, run: dict[str, object]) -> dict[str, object]:
        parsed = CatalogRebuildRunRecord.model_validate(run)
        row = self.session.get(CatalogRebuildRun, parsed.id)
        if row is None:
            row = CatalogRebuildRun(id=parsed.id)
            self.session.add(row)
        row.status = parsed.status.value
        row.triggered_by = parsed.triggered_by
        row.barcodes_planned = json.dumps(parsed.barcodes_planned)
        row.barcodes_completed = json.dumps(parsed.barcodes_completed)
        row.barcodes_failed = json.dumps(parsed.barcodes_failed)
        row.barcodes_skipped = json.dumps(parsed.barcodes_skipped)
        row.files_recovered = parsed.files_recovered
        row.datasets_recovered = parsed.datasets_recovered
        row.path_mappings_recovered = parsed.path_mappings_recovered
        row.error_summary = json.dumps(parsed.error_summary)
        row.created_at = parsed.created_at
        row.updated_at = parsed.updated_at
        row.completed_at = parsed.completed_at
        self.session.commit()
        self.session.refresh(row)
        return self._catalog_rebuild_run_to_dict(row)

    def get_rebuild_run(self, run_id: str) -> dict[str, object] | None:
        row = self.session.get(CatalogRebuildRun, run_id)
        if row is None:
            return None
        return self._catalog_rebuild_run_to_dict(row)

    def update_rebuild_run(self, run_id: str, updates: dict[str, object]) -> dict[str, object] | None:
        existing = self.get_rebuild_run(run_id)
        if existing is None:
            return None
        parsed = CatalogRebuildRunRecord.model_validate({**existing, **updates})
        row = self.session.get(CatalogRebuildRun, run_id)
        assert row is not None
        row.status = parsed.status.value
        row.triggered_by = parsed.triggered_by
        row.barcodes_planned = json.dumps(parsed.barcodes_planned)
        row.barcodes_completed = json.dumps(parsed.barcodes_completed)
        row.barcodes_failed = json.dumps(parsed.barcodes_failed)
        row.barcodes_skipped = json.dumps(parsed.barcodes_skipped)
        row.files_recovered = parsed.files_recovered
        row.datasets_recovered = parsed.datasets_recovered
        row.path_mappings_recovered = parsed.path_mappings_recovered
        row.error_summary = json.dumps(parsed.error_summary)
        row.created_at = parsed.created_at
        row.updated_at = parsed.updated_at
        row.completed_at = parsed.completed_at
        self.session.commit()
        self.session.refresh(row)
        return self._catalog_rebuild_run_to_dict(row)

    def list_rebuild_runs(self, limit: int = 50) -> list[dict[str, object]]:
        stmt = select(CatalogRebuildRun).order_by(CatalogRebuildRun.created_at.desc()).limit(limit)
        rows = self.session.execute(stmt).scalars().all()
        return [self._catalog_rebuild_run_to_dict(row) for row in rows]

    def create_manifest_version(self, version: dict[str, object]) -> dict[str, object]:
        parsed = ManifestVersionRecord.model_validate(version)
        row = self.session.get(ManifestVersion, parsed.id)
        if row is None:
            row = ManifestVersion(id=parsed.id)
            self.session.add(row)
        row.barcode = parsed.barcode
        row.version_ts = parsed.version_ts
        row.manifest_path = parsed.manifest_path
        row.sha256 = parsed.sha256
        row.file_count = parsed.file_count
        row.is_current = parsed.is_current
        row.recorded_at = parsed.recorded_at
        self.session.commit()
        self.session.refresh(row)
        return self._manifest_version_to_dict(row)

    def list_manifest_versions(self, barcode: str) -> list[dict[str, object]]:
        stmt = (
            select(ManifestVersion)
            .where(ManifestVersion.barcode == barcode)
            .order_by(ManifestVersion.version_ts.desc(), ManifestVersion.recorded_at.desc())
        )
        rows = self.session.execute(stmt).scalars().all()
        return [self._manifest_version_to_dict(row) for row in rows]

    def upsert_path_mapping(self, record: PathMappingRecord) -> PathMappingRecord:
        """Insert or update a PathMapping row. Validates through Pydantic before persisting."""
        parsed = PathMappingRecord.model_validate(record.model_dump(mode="json"))
        row = (
            self.session.execute(
                select(PathMapping).where(
                    PathMapping.logical_path == parsed.logical_path,
                    PathMapping.pool_id == parsed.pool_id,
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            row = PathMapping(
                id=parsed.id,
                logical_path=parsed.logical_path,
                pool_id=parsed.pool_id,
                created_at=parsed.created_at,
            )
            self.session.add(row)
        row.id = row.id or parsed.id
        row.logical_path = parsed.logical_path
        row.pool_id = parsed.pool_id
        row.dataset_id = parsed.dataset_id
        row.primary_barcode = parsed.primary_barcode
        row.all_barcodes = json.dumps(parsed.all_barcodes)
        row.file_record_id = parsed.file_record_id
        row.file_state = parsed.file_state.value
        row.restore_strategy = parsed.restore_strategy
        row.size = parsed.size
        row.checksum = parsed.checksum
        row.last_seen_at = parsed.last_seen_at
        row.created_at = row.created_at or parsed.created_at or _utcnow_iso() + "Z"
        row.updated_at = _utcnow_iso() + "Z"
        self.session.commit()
        self.session.refresh(row)
        return PathMappingRecord.model_validate(self._path_mapping_to_dict(row))

    def get_path_mapping(self, logical_path: str, pool_id: str = "") -> PathMappingRecord | None:
        """Lookup a single path mapping by (logical_path, pool_id). Returns None if not found."""
        row = (
            self.session.execute(
                select(PathMapping).where(
                    PathMapping.logical_path == logical_path,
                    PathMapping.pool_id == pool_id,
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return None
        return PathMappingRecord.model_validate(self._path_mapping_to_dict(row))

    def search_path_mappings(self, req: PathMappingSearchRequest) -> list[PathMappingRecord]:
        """Filter path mappings. Supports prefix, pool_id, dataset_id, barcode, file_state."""
        stmt = select(PathMapping)
        if req.prefix:
            stmt = stmt.where(PathMapping.logical_path.startswith(req.prefix))
        if req.pool_id:
            stmt = stmt.where(PathMapping.pool_id == req.pool_id)
        if req.dataset_id:
            stmt = stmt.where(PathMapping.dataset_id == req.dataset_id)
        if req.barcode:
            stmt = stmt.where(
                (PathMapping.primary_barcode == req.barcode)
                | (PathMapping.all_barcodes.like(f'%"{req.barcode}"%'))
            )
        if req.file_state is not None:
            stmt = stmt.where(PathMapping.file_state == req.file_state.value)
        stmt = stmt.order_by(PathMapping.logical_path).offset(req.offset).limit(req.limit)
        rows = self.session.execute(stmt).scalars().all()
        return [PathMappingRecord.model_validate(self._path_mapping_to_dict(row)) for row in rows]

    def delete_path_mapping(self, logical_path: str, pool_id: str = "") -> bool:
        """Delete a path mapping. Returns True if a row was deleted."""
        row = (
            self.session.execute(
                select(PathMapping).where(
                    PathMapping.logical_path == logical_path,
                    PathMapping.pool_id == pool_id,
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def bulk_upsert_path_mappings(self, entries: list[PathMappingRecord]) -> int:
        """Bulk upsert. Returns count of rows upserted."""
        for entry in entries:
            self.upsert_path_mapping(entry)
        return len(entries)

    def count_path_mappings(self, pool_id: str = "", dataset_id: str = "") -> int:
        """Count path mappings, optionally filtered by pool_id or dataset_id."""
        stmt = select(func.count()).select_from(PathMapping)
        if pool_id:
            stmt = stmt.where(PathMapping.pool_id == pool_id)
        if dataset_id:
            stmt = stmt.where(PathMapping.dataset_id == dataset_id)
        return int(self.session.execute(stmt).scalar_one())

    def save_safety_token(self, token: SafetyToken) -> None:
        row = SafetyTokenRecord(
            token=token.token,
            operation=token.operation,
            target_barcode=token.target_barcode,
            expires_at=token.expires_at,
        )
        self.session.merge(row)
        self.session.commit()

    def get_safety_token(self, token_value: str) -> SafetyToken | None:
        row = self.session.get(SafetyTokenRecord, token_value)
        if row is None:
            return None
        return SafetyToken(
            token=row.token,
            operation=row.operation,
            target_barcode=row.target_barcode,
            expires_at=row.expires_at,
        )

    def delete_safety_token(self, token_value: str) -> None:
        row = self.session.get(SafetyTokenRecord, token_value)
        if row is not None:
            self.session.delete(row)
            self.session.commit()
