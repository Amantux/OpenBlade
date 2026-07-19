"""NAS configuration persistence service."""

from __future__ import annotations

import json
from pathlib import PurePosixPath

from openblade.catalog.models import Cartridge, NasCacheDrive, NasShare, NasStoragePolicy
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    CacheDriveConfig,
    NasDataset,
    NasFileRecord,
    NasFileState,
    NasPool,
    NasRestoreJob,
    NasShareDefinition,
    SourceStreamConfig,
    StoragePolicy,
)

_SOURCE_STREAM_CONFIG_KEY = "source_stream"


class NasService:
    def __init__(self, repository: CatalogRepository) -> None:
        self.repository = repository

    def _policy_from_row(self, row: NasStoragePolicy) -> StoragePolicy:
        data = json.loads(row.config_json or "{}")
        data.setdefault("id", row.id)
        data.setdefault("name", row.name)
        data.setdefault("policy_type", row.policy_type)
        return StoragePolicy.model_validate(data)

    def _cache_drive_from_row(self, row: NasCacheDrive) -> CacheDriveConfig:
        data = json.loads(row.config_json or "{}")
        data.setdefault("id", row.id)
        data.setdefault("name", row.name)
        data.setdefault("root_path", row.root_path)
        data.setdefault("enabled", row.enabled)
        return CacheDriveConfig.model_validate(data)

    def _share_from_row(self, row: NasShare) -> NasShareDefinition:
        data = json.loads(row.config_json or "{}")
        data.setdefault("path", row.path)
        data.setdefault("name", row.name)
        data.setdefault("share_type", row.share_type)
        return NasShareDefinition.model_validate(data)

    def get_policies(self) -> list[StoragePolicy]:
        return [self._policy_from_row(row) for row in self.repository.list_nas_policies()]

    def get_policy(self, policy_id: str) -> StoragePolicy | None:
        row = self.repository.get_nas_policy(policy_id)
        if row is None:
            return None
        return self._policy_from_row(row)

    def upsert_policy(self, policy: StoragePolicy) -> StoragePolicy:
        row = self.repository.upsert_nas_policy(policy.id, policy.model_dump(mode="json"))
        return self._policy_from_row(row)

    def delete_policy(self, policy_id: str) -> bool:
        for share_row in self.repository.list_nas_shares():
            share = self._share_from_row(share_row)
            if share.default_policy_id == policy_id:
                raise ValueError(f"Policy {policy_id} is still referenced by share {share.path}")
        return self.repository.delete_nas_policy(policy_id)

    def get_cache_drives(self) -> list[CacheDriveConfig]:
        return [self._cache_drive_from_row(row) for row in self.repository.list_nas_cache_drives()]

    def get_cache_drive(self, drive_id: str) -> CacheDriveConfig | None:
        row = self.repository.get_nas_cache_drive(drive_id)
        if row is None:
            return None
        return self._cache_drive_from_row(row)

    def upsert_cache_drive(self, cfg: CacheDriveConfig) -> CacheDriveConfig:
        row = self.repository.upsert_nas_cache_drive(cfg.id, cfg.model_dump(mode="json"))
        return self._cache_drive_from_row(row)

    def delete_cache_drive(self, drive_id: str) -> bool:
        return self.repository.delete_nas_cache_drive(drive_id)

    def get_source_stream_config(self) -> SourceStreamConfig:
        data = self.repository.get_nas_config(_SOURCE_STREAM_CONFIG_KEY)
        if data is None:
            return SourceStreamConfig()
        return SourceStreamConfig.model_validate(data)

    def update_source_stream_config(self, cfg: SourceStreamConfig) -> SourceStreamConfig:
        self.repository.set_nas_config(_SOURCE_STREAM_CONFIG_KEY, cfg.model_dump(mode="json"))
        return self.get_source_stream_config()

    def list_source_stream_configs(self) -> list[SourceStreamConfig]:
        data = self.repository.get_nas_config(_SOURCE_STREAM_CONFIG_KEY)
        if data is None:
            return []
        return [SourceStreamConfig.model_validate(data)]

    def delete_source_stream_config(self) -> bool:
        return self.repository.delete_nas_config(_SOURCE_STREAM_CONFIG_KEY)

    def get_nas_shares(self) -> list[NasShareDefinition]:
        return [self._share_from_row(row) for row in self.repository.list_nas_shares()]

    def get_share(self, path: str) -> NasShareDefinition | None:
        row = self.repository.get_nas_share(path)
        if row is None:
            return None
        return self._share_from_row(row)

    def upsert_share(self, share: NasShareDefinition) -> NasShareDefinition:
        if (
            share.default_policy_id is not None
            and self.repository.get_nas_policy(share.default_policy_id) is None
        ):
            raise ValueError(f"Policy {share.default_policy_id} not found")
        for pool_id in share.pool_ids:
            if self.repository.get_nas_pool(pool_id) is None:
                raise ValueError(f"Pool {pool_id} not found")
        row = self.repository.upsert_nas_share(share.path, share.model_dump(mode="json"))
        return self._share_from_row(row)

    def delete_share(self, path: str) -> bool:
        return self.repository.delete_nas_share(path)

    def list_pools(self) -> list[NasPool]:
        return [NasPool.model_validate(row) for row in self.repository.list_nas_pools()]

    def get_pool(self, pool_id: str) -> NasPool | None:
        row = self.repository.get_nas_pool(pool_id)
        if row is None:
            return None
        return NasPool.model_validate(row)

    def upsert_pool(self, pool: NasPool) -> NasPool:
        return NasPool.model_validate(self.repository.upsert_nas_pool(pool.model_dump(mode="json")))

    def delete_pool(self, pool_id: str) -> bool:
        return self.repository.delete_nas_pool(pool_id)

    def _normalize_logical_path(self, path: str) -> str:
        normalized = str(PurePosixPath("/" + str(path or "").lstrip("/"))).lstrip("/")
        return "" if normalized == "." else normalized

    def _list_pool_file_records(self, pool_id: str) -> list[NasFileRecord]:
        records: list[NasFileRecord] = []
        for dataset in self.list_datasets(pool_id):
            records.extend(self.list_file_records(dataset.id))
        return sorted(records, key=lambda record: (record.relative_path, record.id))

    def list_pool_file_records(self, pool_id: str) -> list[NasFileRecord]:
        return self._list_pool_file_records(pool_id)

    def derive_file_state(
        self,
        record: NasFileRecord,
        loaded_tapes: list[str] | None = None,
    ) -> NasFileState:
        if record.status is NasFileState.HYDRATING:
            return NasFileState.HYDRATING
        if record.status is NasFileState.FAILED:
            return NasFileState.FAILED
        if record.status is NasFileState.CORRUPT:
            return NasFileState.CORRUPT
        if record.status is NasFileState.EXPORTED:
            return NasFileState.EXPORTED
        if record.tape_barcode is None:
            return NasFileState.MISSING_TAPE
        if loaded_tapes is not None:
            loaded_tape_set = set(loaded_tapes)
            return (
                NasFileState.ONLINE_CACHED
                if record.tape_barcode in loaded_tape_set
                else NasFileState.OFFLINE_ON_TAPE
            )
        return record.status

    def browse_pool(self, pool_id: str, path: str = "") -> dict[str, object]:
        if self.get_pool(pool_id) is None:
            raise KeyError("pool not found")

        requested_path = self._normalize_logical_path(path)
        prefix = f"{requested_path}/" if requested_path else ""
        directory_entries: dict[str, dict[str, object]] = {}
        file_entries: list[dict[str, object]] = []
        total_files = 0
        total_bytes = 0
        offline_count = 0
        online_count = 0
        hydrating_count = 0

        for record in self._list_pool_file_records(pool_id):
            logical_path = self._normalize_logical_path(record.relative_path)
            if requested_path:
                if logical_path == requested_path:
                    remainder = PurePosixPath(logical_path).name
                elif logical_path.startswith(prefix):
                    remainder = logical_path[len(prefix) :]
                else:
                    continue
            else:
                remainder = logical_path

            parts = [part for part in remainder.split("/") if part]
            if not parts:
                continue

            if len(parts) > 1:
                directory_name = parts[0]
                directory_entries.setdefault(
                    directory_name,
                    {
                        "name": directory_name,
                        "type": "directory",
                        "size_bytes": 0,
                        "mtime": None,
                        "state": None,
                        "tape_barcode": None,
                        "checksum_sha256": None,
                        "logical_path": "/".join(
                            segment for segment in (requested_path, directory_name) if segment
                        ),
                    },
                )
                continue

            state = self.derive_file_state(record, loaded_tapes=None)
            file_entries.append(
                {
                    "name": parts[0],
                    "type": "file",
                    "size_bytes": record.size_bytes,
                    "mtime": record.mtime,
                    "state": state,
                    "tape_barcode": record.tape_barcode,
                    "checksum_sha256": record.checksum_sha256,
                    "logical_path": logical_path,
                }
            )
            total_files += 1
            total_bytes += record.size_bytes
            if state is NasFileState.ONLINE_CACHED:
                online_count += 1
            elif state is NasFileState.OFFLINE_ON_TAPE:
                offline_count += 1
            elif state is NasFileState.HYDRATING:
                hydrating_count += 1

        entries = sorted(
            [*directory_entries.values(), *file_entries],
            key=lambda entry: (entry["type"] != "directory", entry["name"]),
        )
        return {
            "pool_id": pool_id,
            "path": requested_path,
            "entries": entries,
            "total_files": total_files,
            "total_bytes": total_bytes,
            "offline_count": offline_count,
            "online_count": online_count,
            "hydrating_count": hydrating_count,
        }

    def get_pool_file_detail(self, pool_id: str, logical_path: str) -> NasFileRecord:
        if self.get_pool(pool_id) is None:
            raise KeyError("pool not found")

        normalized_path = self._normalize_logical_path(logical_path)
        for record in self._list_pool_file_records(pool_id):
            if self._normalize_logical_path(record.relative_path) == normalized_path:
                return record
        raise KeyError("file not found")

    def list_datasets(self, pool_id: str | None = None) -> list[NasDataset]:
        return [NasDataset.model_validate(row) for row in self.repository.list_nas_datasets(pool_id)]

    def get_dataset(self, dataset_id: str) -> NasDataset | None:
        row = self.repository.get_nas_dataset(dataset_id)
        if row is None:
            return None
        return NasDataset.model_validate(row)

    def get_dataset_detail(self, dataset_id: str) -> dict[str, object]:
        """Returns dataset + tape_set + shard_map + file_count + total_bytes + copies_completed + policy_name."""
        dataset = self.get_dataset(dataset_id)
        if dataset is None:
            raise KeyError("dataset not found")

        records = self.list_file_records(dataset_id)
        shard_map: dict[str, list[str]] = {}
        total_bytes = 0
        for record in records:
            total_bytes += record.size_bytes
            if record.tape_barcode is None:
                continue
            shard_map.setdefault(record.tape_barcode, []).append(record.relative_path)

        for logical_paths in shard_map.values():
            logical_paths.sort()

        tape_set = sorted(shard_map)
        policy_name = None
        if dataset.policy_id is not None:
            policy = self.get_policy(dataset.policy_id)
            policy_name = None if policy is None else policy.name

        detail = dataset.model_dump(mode="json")
        detail.update(
            {
                "tape_set": tape_set,
                "shard_map": shard_map,
                "file_count": len(records),
                "total_bytes": total_bytes,
                "copies_completed": len(tape_set),
                "policy_name": policy_name,
            }
        )
        return detail

    def upsert_dataset(self, dataset: NasDataset) -> NasDataset:
        return NasDataset.model_validate(
            self.repository.upsert_nas_dataset(dataset.model_dump(mode="json"))
        )

    def delete_dataset(self, dataset_id: str) -> bool:
        return self.repository.delete_nas_dataset(dataset_id)

    def update_cartridge(
        self,
        barcode: str,
        *,
        volume_group_id: str | None = None,
        used_bytes: int | None = None,
        capacity_bytes: int | None = None,
        formatted: bool | None = None,
    ) -> Cartridge:
        cartridge = self.repository.add_cartridge(barcode, volume_group_id)
        if used_bytes is not None:
            cartridge.used_bytes = used_bytes
        if capacity_bytes is not None:
            cartridge.capacity_bytes = capacity_bytes
        if formatted is not None:
            cartridge.formatted = formatted
        self.repository.session.commit()
        self.repository.session.refresh(cartridge)
        return cartridge

    def list_file_records(self, dataset_id: str) -> list[NasFileRecord]:
        return [
            NasFileRecord.model_validate(row)
            for row in self.repository.list_nas_file_records(dataset_id)
        ]

    def get_file_record(self, file_id: str) -> NasFileRecord | None:
        row = self.repository.get_nas_file_record(file_id)
        if row is None:
            return None
        return NasFileRecord.model_validate(row)

    def upsert_file_record(self, file_record: NasFileRecord) -> NasFileRecord:
        return NasFileRecord.model_validate(
            self.repository.upsert_nas_file_record(file_record.model_dump(mode="json"))
        )

    def update_file_status(self, file_id: str, status: str) -> bool:
        return self.repository.update_nas_file_status(file_id, status)

    def list_restore_jobs(self, status: str | None = None) -> list[NasRestoreJob]:
        return [
            NasRestoreJob.model_validate(row)
            for row in self.repository.list_nas_restore_jobs(status)
        ]

    def get_restore_job(self, job_id: str) -> NasRestoreJob | None:
        row = self.repository.get_nas_restore_job(job_id)
        if row is None:
            return None
        return NasRestoreJob.model_validate(row)

    def upsert_restore_job(self, job: NasRestoreJob) -> NasRestoreJob:
        return NasRestoreJob.model_validate(
            self.repository.upsert_nas_restore_job(job.model_dump(mode="json"))
        )

    def update_restore_job_status(
        self,
        job_id: str,
        status: str,
        bytes_restored: int | None = None,
        files_restored: int | None = None,
        files_failed: int | None = None,
        partial_success: bool | None = None,
        error_message: str | None = None,
    ) -> bool:
        return self.repository.update_nas_restore_job_status(
            job_id,
            status,
            bytes_restored=bytes_restored,
            files_restored=files_restored,
            files_failed=files_failed,
            partial_success=partial_success,
            error_message=error_message,
        )

    def delete_restore_job(self, job_id: str) -> bool:
        return self.repository.delete_nas_restore_job(job_id)

    def get_default_shares(self) -> list[NasShareDefinition]:
        return [
            NasShareDefinition(
                path="/openblade/inbox",
                name="OpenBlade Inbox",
                share_type="inbox",
                default_policy_id="balanced",
                writable=True,
                description="Default ingest inbox for standard archive jobs.",
            ),
            NasShareDefinition(
                path="/openblade/inbox-critical",
                name="Critical Inbox",
                share_type="inbox",
                default_policy_id="critical_sequential",
                writable=True,
                description="Ingest inbox for critical sequential archive jobs.",
            ),
            NasShareDefinition(
                path="/openblade/inbox-sharded",
                name="Sharded Inbox",
                share_type="inbox",
                default_policy_id="noncritical_sharded",
                writable=True,
                description="Ingest inbox for sharded archive jobs.",
            ),
            NasShareDefinition(
                path="/openblade/restore",
                name="Restore Output",
                share_type="restore",
                writable=True,
                description="Restore target for recovered datasets.",
            ),
            NasShareDefinition(
                path="/openblade/catalog",
                name="Catalog View",
                share_type="catalog",
                description="Read-only catalog namespace for archived content.",
            ),
            NasShareDefinition(
                path="/openblade/virtual",
                name="Virtual Namespace",
                share_type="virtual",
                description="Virtualized namespace spanning archive content.",
            ),
        ]
