"""NAS configuration persistence service."""

from __future__ import annotations

import json

from openblade.catalog.models import NasCacheDrive, NasShare, NasStoragePolicy
from openblade.catalog.repository import CatalogRepository
from openblade.nas.types import (
    CacheDriveConfig,
    NasDataset,
    NasFileRecord,
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

    def list_datasets(self, pool_id: str | None = None) -> list[NasDataset]:
        return [NasDataset.model_validate(row) for row in self.repository.list_nas_datasets(pool_id)]

    def get_dataset(self, dataset_id: str) -> NasDataset | None:
        row = self.repository.get_nas_dataset(dataset_id)
        if row is None:
            return None
        return NasDataset.model_validate(row)

    def upsert_dataset(self, dataset: NasDataset) -> NasDataset:
        return NasDataset.model_validate(
            self.repository.upsert_nas_dataset(dataset.model_dump(mode="json"))
        )

    def delete_dataset(self, dataset_id: str) -> bool:
        return self.repository.delete_nas_dataset(dataset_id)

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
        error_message: str | None = None,
    ) -> bool:
        return self.repository.update_nas_restore_job_status(
            job_id,
            status,
            bytes_restored=bytes_restored,
            files_restored=files_restored,
            files_failed=files_failed,
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

