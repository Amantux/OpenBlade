<!-- GENERATED FILE -- do not edit by hand.
     Regenerate with: python3 tools/gen_wiki_reference.py
     Guarded by: tests/unit/test_wiki_reference_generated.py -->

# HTTP API reference

Introspected from the OpenAPI schema of `openblade.api.main:app`.

**1171 operations** total: 191 on the native OpenBlade control plane, 980 on the Quantum AML / iBlade emulator surface.

The application serves two surfaces from one ASGI app. Setting
`OPENBLADE_SCALAR_API_ONLY=true` puts it in emulator-only mode, where the
native surfaces below return 404.

Interactive docs for a running instance are at `/docs` and `/redoc`.

## Native OpenBlade control plane

### `health` (7 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/error-codes` | Get Error Codes | - | - | `ErrorCodesResponse` |
| `GET` | `/healthz` | Get Health | - | - | `HealthResponse` |
| `GET` | `/readyz` | Get Ready | - | - | `ReadyResponse` |
| `GET` | `/status/catalog` | Get Catalog Status | - | - | `CatalogStatusResponse` |
| `GET` | `/status/library` | Get Library Status | - | - | `openblade__nas__types__LibraryStatusResponse` |
| `GET` | `/system/config-summary` | Get System Config Summary | - | - | `SystemConfigSummaryResponse` |
| `GET` | `/version` | Get Version | - | - | `VersionResponse` |

### `inventory` (1 operation)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/inventory/` | Get Inventory | - | - | `InventoryResponse` |

### `cartridges` (3 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/cartridges/` | List Cartridges | - | - | `CartridgeResponse[]` |
| `POST` | `/cartridges/format/confirm` | Format Confirm | - | `FormatConfirmRequest` | `OperationResponse` |
| `POST` | `/cartridges/{barcode}/format/dry-run` | Format Dry Run | `barcode` (path) | - | `DryRunResponse` |

### `tape-ops` (3 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/tape-ops` | List Tape Ops | `barcode`? (query), `status`? (query), `limit`? (query) | - | `TapeOpRecord[]` |
| `POST` | `/tape-ops/execute` | Execute Tape Op | - | `TapeOpRequest` | `TapeOpRecord` |
| `GET` | `/tape-ops/{op_id}` | Get Tape Op | `op_id` (path) | - | `TapeOpRecord` |

### `ltfs` (6 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/ltfs/browse` | Browse Ltfs Catalog | `tape_barcode`? (query), `path_prefix`? (query) | - | `LtfsBrowseEntryResponse[]` |
| `POST` | `/ltfs/format` | Ltfs Format | - | `object` | `object` |
| `POST` | `/ltfs/mount` | Ltfs Mount | - | `object` | `object` |
| `GET` | `/ltfs/status` | Ltfs Status | - | - | `object` |
| `GET` | `/ltfs/tapes` | List Ltfs Catalog Tapes | - | - | `string[]` |
| `POST` | `/ltfs/unmount` | Ltfs Unmount | - | `object` | `object` |

### `volume-groups` (3 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/volume-groups/` | List Volume Groups | - | - | `VolumeGroupResponse[]` |
| `POST` | `/volume-groups/` | Create Volume Group | - | `VolumeGroupCreateRequest` | `VolumeGroupResponse` |
| `POST` | `/volume-groups/{name}/assign` | Assign Cartridge | `name` (path) | `AssignCartridgeRequest` | `VolumeGroupResponse` |

### `archive` (2 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/archive/` | Enqueue Archive | - | `ArchiveRequest` | `EnqueuedJobResponse` |
| `POST` | `/archive/sharded` | Enqueue Sharded Archive | - | `ShardedArchiveApiRequest` | `EnqueuedJobResponse` |

### `restore` (1 operation)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/restore/` | Enqueue Restore | - | `openblade__api__routes_restore__RestoreRequest` | `EnqueuedJobResponse` |

### `jobs` (2 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/jobs/` | List Jobs | `library_id`? (query) | - | `openblade__api__routes_jobs__JobResponse[]` |
| `GET` | `/jobs/{job_id}` | Get Job | `job_id` (path) | - | `openblade__api__routes_jobs__JobResponse` |

### `catalog` (6 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/catalog/` | List Catalog Files | `limit`? (query), `offset`? (query), `search`? (query) | - | `CatalogListResponse` |
| `GET` | `/catalog/seed-demo` | Seed Demo Catalog | - | - | `CatalogSeedDemoResponse` |
| `GET` | `/catalog/{file_id}` | Get Catalog File | `file_id` (path) | - | `CatalogFileDetailResponse` |
| `DELETE` | `/catalog/{file_id}` | Delete Catalog File | `file_id` (path) | - | (no body) |
| `GET` | `/catalog/{file_id}/instances` | List Catalog File Instances | `file_id` (path) | - | `FileInstanceResponse[]` |
| `GET` | `/catalog/{file_id}/shards` | List Catalog File Shards | `file_id` (path) | - | `CatalogFileDetailResponse[]` |

### `virtual` (6 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/virtual/hydrate` | Create Hydration Job | - | `HydrationRequest` | `HydrationJob` |
| `GET` | `/virtual/jobs` | List Hydration Jobs | - | - | `HydrationJob[]` |
| `GET` | `/virtual/jobs/{job_id}` | Get Hydration Job | `job_id` (path) | - | `HydrationJob` |
| `DELETE` | `/virtual/jobs/{job_id}` | Cancel Hydration Job | `job_id` (path) | - | `HydrationJob` |
| `GET` | `/virtual/ls` | List Virtual Directory | `path`? (query) | - | `VirtualDirectoryListing` |
| `GET` | `/virtual/stat` | Stat Virtual Path | `path` (query) | - | `VirtualFileEntry` |

### `NAS Config` (118 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/nas/archive-plan` | Archive Plan | - | `ArchivePlanRequest` | `ArchivePlan` |
| `GET` | `/nas/cache-drives` | List Cache Drives | - | - | `CacheDriveConfig[]` |
| `POST` | `/nas/cache-drives` | Create Or Update Cache Drive | - | `CacheDriveConfig` | `CacheDriveConfig` |
| `GET` | `/nas/cache-drives/{drive_id}` | Get Cache Drive | `drive_id` (path) | - | `CacheDriveConfig` |
| `DELETE` | `/nas/cache-drives/{drive_id}` | Delete Cache Drive | `drive_id` (path) | - | `object` |
| `GET` | `/nas/catalog/manifest-versions/{barcode}` | List Catalog Manifest Versions | `barcode` (path) | - | `ManifestVersionRecord[]` |
| `POST` | `/nas/catalog/rebuild/activate` | Activate Catalog Rebuild | - | `RebuildActivationRequest` | `RebuildActivationResult` |
| `GET` | `/nas/catalog/rebuild/loaded-tapes` | List Catalog Rebuild Loaded Tapes | - | - | `string[]` |
| `POST` | `/nas/catalog/rebuild/plan` | Plan Catalog Rebuild | - | `RebuildPlanRequest` | `RebuildPlanResult` |
| `GET` | `/nas/catalog/rebuild/runs` | List Catalog Rebuild Runs | `limit`? (query) | - | `CatalogRebuildRunRecord[]` |
| `GET` | `/nas/catalog/rebuild/{run_id}` | Get Catalog Rebuild Run | `run_id` (path) | - | `CatalogRebuildRunRecord` |
| `POST` | `/nas/catalog/rebuild/{run_id}/execute` | Execute Catalog Rebuild | `run_id` (path) | - | `CatalogRebuildRunRecord` |
| `GET` | `/nas/datasets` | List Datasets | `pool_id`? (query), `status`? (query) | - | `object[]` |
| `GET` | `/nas/datasets/{dataset_id}` | Get Dataset Detail | `dataset_id` (path) | - | `object` |
| `POST` | `/nas/datasets/{dataset_id}/export` | Export Dataset | `dataset_id` (path) | - | `object` |
| `GET` | `/nas/datasets/{dataset_id}/files` | List Dataset Files | `dataset_id` (path), `skip`? (query), `limit`? (query) | - | `NasFileRecord[]` |
| `GET` | `/nas/datasets/{dataset_id}/manifest` | Get Dataset Manifest | `dataset_id` (path) | - | `object` |
| `GET` | `/nas/datasets/{dataset_id}/report` | Get Dataset Report | `dataset_id` (path) | - | `object` |
| `POST` | `/nas/datasets/{dataset_id}/verify` | Verify Dataset | `dataset_id` (path) | - | `object` |
| `GET` | `/nas/fuse/log` | Get Fuse Log | - | - | `object[]` |
| `POST` | `/nas/fuse/open` | Open Virtual File | - | `FuseOpenRequest` | `object` |
| `POST` | `/nas/ingest/start` | Start Ingest | - | `StartIngestRequest` | `StartIngestResponse` |
| `GET` | `/nas/ingest/{job_id}` | Ingest Status | `job_id` (path) | - | `IngestJob` |
| `POST` | `/nas/ingest/{job_id}/cancel` | Cancel Ingest | `job_id` (path) | - | `CancelIngestResponse` |
| `POST` | `/nas/path-mappings` | Upsert Path Mapping | - | `PathMappingRecord` | `PathMappingRecord` |
| `DELETE` | `/nas/path-mappings` | Delete Path Mapping | `path` (query), `pool_id`? (query) | - | `object` |
| `POST` | `/nas/path-mappings/bulk` | Bulk Upsert Path Mappings | - | `PathMappingBulkUpsertRequest` | `object` |
| `GET` | `/nas/path-mappings/lookup` | Lookup Path Mapping | `path` (query), `pool_id`? (query) | - | `PathLookupResult` |
| `POST` | `/nas/path-mappings/search` | Search Path Mappings | - | `PathMappingSearchRequest` | `PathMappingRecord[]` |
| `GET` | `/nas/path-mappings/stats` | Get Path Mapping Stats | `pool_id`? (query), `dataset_id`? (query) | - | `object` |
| `GET` | `/nas/policies` | List Policies | - | - | `StoragePolicy[]` |
| `POST` | `/nas/policies` | Create Or Update Policy | - | `StoragePolicy` | `StoragePolicy` |
| `GET` | `/nas/policies/{policy_id}` | Get Policy | `policy_id` (path) | - | `StoragePolicy` |
| `DELETE` | `/nas/policies/{policy_id}` | Delete Policy | `policy_id` (path) | - | `object` |
| `GET` | `/nas/pools` | List Pools | - | - | `NasPool[]` |
| `POST` | `/nas/pools` | Create Pool | - | `NasPool` | `NasPool` |
| `GET` | `/nas/pools/{pool_id}` | Get Pool | `pool_id` (path) | - | `NasPool` |
| `PUT` | `/nas/pools/{pool_id}` | Update Pool | `pool_id` (path) | `NasPool` | `NasPool` |
| `DELETE` | `/nas/pools/{pool_id}` | Delete Pool | `pool_id` (path) | - | (no body) |
| `GET` | `/nas/pools/{pool_id}/browse` | Browse Pool | `pool_id` (path), `path`? (query) | - | `object` |
| `GET` | `/nas/pools/{pool_id}/files/{file_path}` | Get Pool File Detail | `pool_id` (path), `file_path` (path) | - | `NasFileRecord` |
| `POST` | `/nas/pools/{pool_id}/request-restore` | Request Restore | `pool_id` (path) | `RestorePlanRequest` | `NasRestoreJob` |
| `POST` | `/nas/resolve-policy` | Resolve Policy | - | `ResolvePolicyRequest` | `EffectivePolicy` |
| `GET` | `/nas/restore-jobs` | List Restore Jobs | - | - | `NasRestoreJob[]` |
| `GET` | `/nas/restore-jobs/{job_id}` | Get Restore Job | `job_id` (path) | - | `NasRestoreJob` |
| `DELETE` | `/nas/restore-jobs/{job_id}` | Cancel Restore Job | `job_id` (path) | - | (no body) |
| `POST` | `/nas/restore-jobs/{job_id}/cancel` | Cancel Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/nas/restore-jobs/{job_id}/pause` | Pause Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/nas/restore-jobs/{job_id}/resume` | Resume Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/nas/restore-jobs/{job_id}/retry` | Retry Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/nas/restore-jobs/{job_id}/run` | Run Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/nas/restore-plan` | Restore Plan | - | `RestorePlanRequest` | `RestorePlan` |
| `GET` | `/nas/shares` | List Shares | - | - | `NasShareDefinition[]` |
| `POST` | `/nas/shares` | Create Or Update Share | - | `NasShareDefinition` | `NasShareDefinition` |
| `GET` | `/nas/shares/{share_id}` | Get Share | `share_id` (path) | - | `NasShareDefinition` |
| `DELETE` | `/nas/shares/{share_id}` | Delete Share | `share_id` (path) | - | `object` |
| `GET` | `/nas/source-stream` | Get Source Stream Config | - | - | `SourceStreamConfig` |
| `PUT` | `/nas/source-stream` | Update Source Stream Config | - | `SourceStreamConfig` | `SourceStreamConfig` |
| `DELETE` | `/nas/source-stream` | Delete Source Stream Config | - | - | `object` |
| `POST` | `/storage/nas/archive-plan` | Archive Plan | - | `ArchivePlanRequest` | `ArchivePlan` |
| `GET` | `/storage/nas/cache-drives` | List Cache Drives | - | - | `CacheDriveConfig[]` |
| `POST` | `/storage/nas/cache-drives` | Create Or Update Cache Drive | - | `CacheDriveConfig` | `CacheDriveConfig` |
| `GET` | `/storage/nas/cache-drives/{drive_id}` | Get Cache Drive | `drive_id` (path) | - | `CacheDriveConfig` |
| `DELETE` | `/storage/nas/cache-drives/{drive_id}` | Delete Cache Drive | `drive_id` (path) | - | `object` |
| `GET` | `/storage/nas/catalog/manifest-versions/{barcode}` | List Catalog Manifest Versions | `barcode` (path) | - | `ManifestVersionRecord[]` |
| `POST` | `/storage/nas/catalog/rebuild/activate` | Activate Catalog Rebuild | - | `RebuildActivationRequest` | `RebuildActivationResult` |
| `GET` | `/storage/nas/catalog/rebuild/loaded-tapes` | List Catalog Rebuild Loaded Tapes | - | - | `string[]` |
| `POST` | `/storage/nas/catalog/rebuild/plan` | Plan Catalog Rebuild | - | `RebuildPlanRequest` | `RebuildPlanResult` |
| `GET` | `/storage/nas/catalog/rebuild/runs` | List Catalog Rebuild Runs | `limit`? (query) | - | `CatalogRebuildRunRecord[]` |
| `GET` | `/storage/nas/catalog/rebuild/{run_id}` | Get Catalog Rebuild Run | `run_id` (path) | - | `CatalogRebuildRunRecord` |
| `POST` | `/storage/nas/catalog/rebuild/{run_id}/execute` | Execute Catalog Rebuild | `run_id` (path) | - | `CatalogRebuildRunRecord` |
| `GET` | `/storage/nas/datasets` | List Datasets | `pool_id`? (query), `status`? (query) | - | `object[]` |
| `GET` | `/storage/nas/datasets/{dataset_id}` | Get Dataset Detail | `dataset_id` (path) | - | `object` |
| `POST` | `/storage/nas/datasets/{dataset_id}/export` | Export Dataset | `dataset_id` (path) | - | `object` |
| `GET` | `/storage/nas/datasets/{dataset_id}/files` | List Dataset Files | `dataset_id` (path), `skip`? (query), `limit`? (query) | - | `NasFileRecord[]` |
| `GET` | `/storage/nas/datasets/{dataset_id}/manifest` | Get Dataset Manifest | `dataset_id` (path) | - | `object` |
| `GET` | `/storage/nas/datasets/{dataset_id}/report` | Get Dataset Report | `dataset_id` (path) | - | `object` |
| `POST` | `/storage/nas/datasets/{dataset_id}/verify` | Verify Dataset | `dataset_id` (path) | - | `object` |
| `GET` | `/storage/nas/fuse/log` | Get Fuse Log | - | - | `object[]` |
| `POST` | `/storage/nas/fuse/open` | Open Virtual File | - | `FuseOpenRequest` | `object` |
| `POST` | `/storage/nas/ingest/start` | Start Ingest | - | `StartIngestRequest` | `StartIngestResponse` |
| `GET` | `/storage/nas/ingest/{job_id}` | Ingest Status | `job_id` (path) | - | `IngestJob` |
| `POST` | `/storage/nas/ingest/{job_id}/cancel` | Cancel Ingest | `job_id` (path) | - | `CancelIngestResponse` |
| `POST` | `/storage/nas/path-mappings` | Upsert Path Mapping | - | `PathMappingRecord` | `PathMappingRecord` |
| `DELETE` | `/storage/nas/path-mappings` | Delete Path Mapping | `path` (query), `pool_id`? (query) | - | `object` |
| `POST` | `/storage/nas/path-mappings/bulk` | Bulk Upsert Path Mappings | - | `PathMappingBulkUpsertRequest` | `object` |
| `GET` | `/storage/nas/path-mappings/lookup` | Lookup Path Mapping | `path` (query), `pool_id`? (query) | - | `PathLookupResult` |
| `POST` | `/storage/nas/path-mappings/search` | Search Path Mappings | - | `PathMappingSearchRequest` | `PathMappingRecord[]` |
| `GET` | `/storage/nas/path-mappings/stats` | Get Path Mapping Stats | `pool_id`? (query), `dataset_id`? (query) | - | `object` |
| `GET` | `/storage/nas/policies` | List Policies | - | - | `StoragePolicy[]` |
| `POST` | `/storage/nas/policies` | Create Or Update Policy | - | `StoragePolicy` | `StoragePolicy` |
| `GET` | `/storage/nas/policies/{policy_id}` | Get Policy | `policy_id` (path) | - | `StoragePolicy` |
| `DELETE` | `/storage/nas/policies/{policy_id}` | Delete Policy | `policy_id` (path) | - | `object` |
| `GET` | `/storage/nas/pools` | List Pools | - | - | `NasPool[]` |
| `POST` | `/storage/nas/pools` | Create Pool | - | `NasPool` | `NasPool` |
| `GET` | `/storage/nas/pools/{pool_id}` | Get Pool | `pool_id` (path) | - | `NasPool` |
| `PUT` | `/storage/nas/pools/{pool_id}` | Update Pool | `pool_id` (path) | `NasPool` | `NasPool` |
| `DELETE` | `/storage/nas/pools/{pool_id}` | Delete Pool | `pool_id` (path) | - | (no body) |
| `GET` | `/storage/nas/pools/{pool_id}/browse` | Browse Pool | `pool_id` (path), `path`? (query) | - | `object` |
| `GET` | `/storage/nas/pools/{pool_id}/files/{file_path}` | Get Pool File Detail | `pool_id` (path), `file_path` (path) | - | `NasFileRecord` |
| `POST` | `/storage/nas/pools/{pool_id}/request-restore` | Request Restore | `pool_id` (path) | `RestorePlanRequest` | `NasRestoreJob` |
| `POST` | `/storage/nas/resolve-policy` | Resolve Policy | - | `ResolvePolicyRequest` | `EffectivePolicy` |
| `GET` | `/storage/nas/restore-jobs` | List Restore Jobs | - | - | `NasRestoreJob[]` |
| `GET` | `/storage/nas/restore-jobs/{job_id}` | Get Restore Job | `job_id` (path) | - | `NasRestoreJob` |
| `DELETE` | `/storage/nas/restore-jobs/{job_id}` | Cancel Restore Job | `job_id` (path) | - | (no body) |
| `POST` | `/storage/nas/restore-jobs/{job_id}/cancel` | Cancel Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/storage/nas/restore-jobs/{job_id}/pause` | Pause Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/storage/nas/restore-jobs/{job_id}/resume` | Resume Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/storage/nas/restore-jobs/{job_id}/retry` | Retry Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/storage/nas/restore-jobs/{job_id}/run` | Run Restore Job Endpoint | `job_id` (path) | - | `NasRestoreJob` |
| `POST` | `/storage/nas/restore-plan` | Restore Plan | - | `RestorePlanRequest` | `RestorePlan` |
| `GET` | `/storage/nas/shares` | List Shares | - | - | `NasShareDefinition[]` |
| `POST` | `/storage/nas/shares` | Create Or Update Share | - | `NasShareDefinition` | `NasShareDefinition` |
| `GET` | `/storage/nas/shares/{share_id}` | Get Share | `share_id` (path) | - | `NasShareDefinition` |
| `DELETE` | `/storage/nas/shares/{share_id}` | Delete Share | `share_id` (path) | - | `object` |
| `GET` | `/storage/nas/source-stream` | Get Source Stream Config | - | - | `SourceStreamConfig` |
| `PUT` | `/storage/nas/source-stream` | Update Source Stream Config | - | `SourceStreamConfig` | `SourceStreamConfig` |
| `DELETE` | `/storage/nas/source-stream` | Delete Source Stream Config | - | - | `object` |

### `upload-download` (6 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `DELETE` | `/api/files/{file_id}` | Delete File | `file_id` (path) | - | `DeleteFileResponse` |
| `GET` | `/api/files/{file_id}/checksum` | Get File Checksum | `file_id` (path) | - | `FileChecksumResponse` |
| `GET` | `/api/files/{file_id}/download` | Download File | `file_id` (path) | - | `application/json` |
| `GET` | `/api/pools/{pool_id}/files` | List Pool Files | `pool_id` (path) | - | `PoolFileListResponse` |
| `POST` | `/api/pools/{pool_id}/push-to-share` | Push Staged Files To Share | `pool_id` (path) | `PushToShareRequest` | `PushToShareResponse` |
| `POST` | `/api/pools/{pool_id}/upload` | Upload File To Pool | `pool_id` (path) | `multipart/form-data` | `UploadResponse` |

### `libraries` (5 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/libraries` | List Libraries | - | - | `openblade__api__routes_libraries__LibraryResponse[]` |
| `POST` | `/api/libraries` | Create Library | - | `LibraryCreate` | `openblade__api__routes_libraries__LibraryResponse` |
| `GET` | `/api/libraries/{library_id}` | Get Library | `library_id` (path) | - | `openblade__api__routes_libraries__LibraryResponse` |
| `PUT` | `/api/libraries/{library_id}` | Update Library | `library_id` (path) | `LibraryUpdate` | `openblade__api__routes_libraries__LibraryResponse` |
| `DELETE` | `/api/libraries/{library_id}` | Delete Library | `library_id` (path) | - | `object` |

### `gateway` (10 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/api/gateway/config` | Get Gateway Config | - | - | `GatewayConfigResponse` |
| `GET` | `/api/gateway/credentials` | List Credentials | - | - | `application/json` |
| `POST` | `/api/gateway/credentials` | Add Credential | - | `CredentialCreate` | `application/json` |
| `PUT` | `/api/gateway/credentials/{username}` | Update Credential | `username` (path) | `CredentialUpdate` | `application/json` |
| `DELETE` | `/api/gateway/credentials/{username}` | Remove Credential | `username` (path) | - | `application/json` |
| `GET` | `/api/gateway/inbox-paths` | List Inbox Paths | - | - | `InboxPathOption[]` |
| `GET` | `/api/gateway/sessions` | List Sessions | `active_only`? (query) | - | `application/json` |
| `POST` | `/api/gateway/start` | Start Gateway | - | - | `GatewayCommandResponse` |
| `GET` | `/api/gateway/status` | Get Gateway Status | - | - | `GatewayStatusResponse` |
| `POST` | `/api/gateway/stop` | Stop Gateway | - | - | `GatewayCommandResponse` |

### `proxy` (1 operation)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/aml/proxy/libraries/{library_id}/probe` | Probe Remote Library | `library_id` (path) | `RemoteLibraryProbeRequest` | `object` |

### `safety` (2 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/safety/check` | Get Safety Check | - | - | `SafetyCheckResponse` |
| `POST` | `/safety/check` | Run Safety Check | - | - | `SafetyCheckResponse` |

### `dashboard` (1 operation)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/dashboard/stats` | Get Dashboard Stats | - | - | `DashboardStatsResponse` |

### `test-runner` (4 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `POST` | `/api/test-runner/run` | Start Test Run | - | `TestRunRequest` | `TestRunResponse` |
| `GET` | `/api/test-runner/runs` | List Runs | - | - | `object[]` |
| `GET` | `/api/test-runner/status/{run_id}` | Get Run Status | `run_id` (path) | - | `TestRunStatus` |
| `GET` | `/api/test-runner/stream/{run_id}` | Stream Run Output | `run_id` (path) | - | `application/json` |

### `(untagged)` (4 operations)

| Method | Path | Summary | Parameters | Request | Response |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/health` | Health | - | - | `object` |
| `POST` | `/restore/plan` | Restore Plan Compat | - | `object` | `object` |
| `POST` | `/storage/archive-planning` | Storage Archive Planning | - | `object` | `ArchivePlanningResponse` |
| `GET` | `/storage/restore-queue` | Storage Restore Queue | - | - | `array` |

## Quantum AML / iBlade emulator surface

These operations implement the Quantum Scalar i3/i6 Web Services wire
contract. They are **not** documented here: the path set is the wire
contract itself and is generated, reviewed and gated separately.

See the generated endpoint catalog at [`quantum_i3_endpoint_catalog.md`](../../../openblade/emulator_contract/quantum_i3_endpoint_catalog.md) and the boundary contract at [`emulator_contract/README.md`](../../../openblade/emulator_contract/README.md).

| Tag | Operations |
| --- | --- |
| `aml-access` | 1 |
| `aml-advanced` | 43 |
| `aml-auth` | 12 |
| `aml-blades` | 33 |
| `aml-diagnostics` | 15 |
| `aml-drives` | 22 |
| `aml-events` | 37 |
| `aml-firmware` | 7 |
| `aml-library` | 3 |
| `aml-matrix-fallback` | 468 |
| `aml-media` | 25 |
| `aml-operations` | 45 |
| `aml-partitions` | 48 |
| `aml-physical` | 30 |
| `aml-system` | 101 |
| `iblade` | 78 |
| `rbac` | 12 |
| **total** | **980** |
