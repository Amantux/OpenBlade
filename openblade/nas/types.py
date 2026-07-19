"""Pydantic models for NAS configuration."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator


class EffectivePolicySource(str, Enum):
    SYSTEM_DEFAULT = "system_default"
    SHARE_DEFAULT = "share_default"
    SIDECAR = "sidecar"



def _strip_required_string(value: object, *, field_name: str, max_length: int | None = None) -> str:
    if value is None:
        raise ValueError(f"{field_name} must be non-empty")
    stripped = str(value).strip()
    if not stripped:
        raise ValueError(f"{field_name} must be non-empty")
    if max_length is not None and len(stripped) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters")
    return stripped


class PolicyType(str, Enum):
    CRITICAL_SEQUENTIAL = "critical_sequential"
    NONCRITICAL_SHARDED = "noncritical_sharded"
    BALANCED = "balanced"


class IngestMode(str, Enum):
    CACHE_DRIVE = "cache_drive"
    SOURCE_STREAM = "source_stream"


class ShardStrategy(str, Enum):
    ROUND_ROBIN = "round_robin"
    CAPACITY_WEIGHTED = "capacity_weighted"
    DIRECTORY_BATCH = "directory_batch"
    HASH_PREFIX = "hash_prefix"
    RESTORE_PARALLELISM_OPTIMIZED = "restore_parallelism_optimized"


class EvictionPolicy(str, Enum):
    NEVER = "never"
    AFTER_VERIFIED = "after_verified"
    AFTER_DAYS = "after_days"
    LRU = "lru"
    MANUAL = "manual"


class DatasetStatus(str, Enum):
    PENDING = "pending"
    ARCHIVING = "archiving"
    ARCHIVED = "archived"
    FAILED = "failed"
    VERIFIED = "verified"
    EXPORTED = "exported"
    CANCELLED = "cancelled"


class HydrationBehavior(str, Enum):
    QUEUE = "queue"
    AUTO = "auto"


class PoolAccessMode(str, Enum):
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class RbacPermission(str, Enum):
    TAPE_READ = "tape:read"
    TAPE_WRITE = "tape:write"
    TAPE_FORMAT = "tape:format"
    TAPE_EJECT = "tape:eject"
    NAS_READ = "nas:read"
    NAS_WRITE = "nas:write"
    NAS_ADMIN = "nas:admin"
    CATALOG_READ = "catalog:read"
    CATALOG_REBUILD = "catalog:rebuild"
    USER_ADMIN = "user:admin"
    TOKEN_MANAGE = "token:manage"
    AUDIT_READ = "audit:read"
    SYSTEM_ADMIN = "system:admin"


class TapeOpType(str, Enum):
    LOAD = "load"
    UNLOAD = "unload"
    FORMAT = "format"
    WRITE = "write"
    READ = "read"
    MOVE = "move"
    VERIFY = "verify"
    EJECT = "eject"


class TapeOpStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class HealthStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    name: str
    status: HealthStatus
    message: str = ""
    latency_ms: float | None = None
    last_checked_at: str = ""


class HealthResponse(BaseModel):
    status: HealthStatus
    components: list[ComponentHealth]
    checked_at: str
    version: str


class ReadyResponse(BaseModel):
    ready: bool
    reason: str = ""
    checked_at: str


class VersionResponse(BaseModel):
    version: str
    git_commit: str = "unknown"
    build_date: str = "unknown"
    python_version: str = ""       # omitted from unauthenticated /version endpoint
    environment: str = ""          # omitted from unauthenticated /version endpoint


class ErrorCodeEntry(BaseModel):
    code: str
    severity: Literal["error", "warning", "info"]
    title: str
    description: str
    action: str


class ErrorCodesResponse(BaseModel):
    error_codes: list[ErrorCodeEntry]


class LibraryStatusResponse(BaseModel):
    library_connected: bool
    drives: list[dict[str, Any]]
    slots_total: int
    slots_occupied: int
    cartridges_loaded: int
    last_updated_at: str


class CatalogStatusResponse(BaseModel):
    db_reachable: bool
    total_datasets: int
    total_file_records: int
    total_path_mappings: int
    total_cartridges: int
    last_rebuild_run_id: str | None
    last_rebuild_status: str | None
    checked_at: str


class TapeOpRequest(BaseModel):
    op_type: TapeOpType
    barcode: str
    drive_id: int | None = None
    slot_id: int | None = None
    tape_path: str | None = None
    content: bytes | None = None
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    requested_by: str = "system"
    job_id: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    extras: dict[str, Any] = Field(default_factory=dict)


class TapeOpRecord(BaseModel):
    op_id: str
    op_type: TapeOpType
    barcode: str
    drive_id: int | None = None
    slot_id: int | None = None
    tape_path: str | None = None
    size_bytes: int | None = None
    checksum_sha256: str | None = None
    requested_by: str
    job_id: str | None = None
    priority: int
    status: TapeOpStatus
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None


class RbacRoleRecord(BaseModel):
    id: str
    name: str
    description: str = ""
    permissions: list[RbacPermission]
    created_at: str
    updated_at: str


class RbacUserRecord(BaseModel):
    id: str
    username: str
    hashed_password: str
    role_id: str
    email: str = ""
    full_name: str = ""
    is_active: bool = True
    is_admin: bool = False
    api_token_ids: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    last_login_at: str | None = None


class RbacApiTokenRecord(BaseModel):
    id: str
    user_id: str
    name: str
    token_hash: str
    permissions: list[RbacPermission]
    expires_at: str | None = None
    created_at: str
    last_used_at: str | None = None
    revoked: bool = False


class RbacAuditEventRecord(BaseModel):
    id: str
    event_type: str
    user_id: str | None = None
    username: str = ""
    resource: str = ""
    action: str = ""
    outcome: str = ""
    details: dict = Field(default_factory=dict)
    created_at: str
    ip_address: str | None = None


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role_id: str
    email: str = ""
    full_name: str = ""
    is_admin: bool = False


class CreateTokenRequest(BaseModel):
    name: str
    permissions: list[RbacPermission]
    expires_at: str | None = None


class CreateTokenResult(BaseModel):
    token_id: str
    raw_token: str
    token_record: RbacApiTokenRecord


class UserSummary(BaseModel):
    """Safe public view of a user — no hashed_password."""

    model_config = ConfigDict(extra="ignore")

    id: str
    username: str
    role_id: str
    email: str
    full_name: str
    is_active: bool
    is_admin: bool
    created_at: str
    last_login_at: str | None = None


class StoragePolicy(BaseModel):
    id: str
    name: str = Field(max_length=64)
    policy_type: PolicyType
    default_ingest_mode: IngestMode = IngestMode.CACHE_DRIVE
    copies_required: int = Field(default=1, ge=1, le=4)
    verify_before_archive: bool = True
    verify_after_archive: bool = True
    allow_spillover: bool = True
    allow_sharding: bool = False
    shard_size_bytes: int | None = Field(default=None, ge=1)
    max_parallelism: int = Field(default=1, ge=1, le=16)
    shard_strategy: ShardStrategy | None = None
    auto_clean_before_archive: bool = True
    manifest_strategy: str = "per_tape"
    cache_retention: EvictionPolicy = EvictionPolicy.AFTER_VERIFIED
    allow_source_delete: bool = False

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> str:
        return _strip_required_string(value, field_name="id")

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _strip_required_string(value, field_name="name", max_length=64)


class SidecarValidationError(Exception):
    def __init__(self, message: str, field: str = None, raw_value=None):
        super().__init__(message)
        self.field = field
        self.raw_value = raw_value


class SidecarPolicy(BaseModel):
    """Parsed contents of a .openblade-policy.yaml file."""

    model_config = ConfigDict(extra="ignore")
    _warnings: list[str] = PrivateAttr(default_factory=list)

    volume_group: str | None = None
    pool: str | None = None
    policy: str | None = None
    ingest_mode: IngestMode | None = None
    cache_drive: str | None = None
    retention: str | None = None
    copies: int | None = Field(default=None, ge=1, le=4)
    preserve_tree: bool | None = None
    verify_before_archive: bool | None = None
    verify_after_write: bool | None = None
    evict_cache_after_verified: bool | None = None


class EffectivePolicy(BaseModel):
    """Resolved effective policy for an ingest operation."""

    policy_name: str | None = None
    policy_id: str | None = None
    ingest_mode: IngestMode = IngestMode.CACHE_DRIVE
    pool: str | None = None
    volume_group: str | None = None
    cache_drive: str | None = None
    copies: int = 1
    verify_before_archive: bool = True
    verify_after_write: bool = True
    evict_cache_after_verified: bool = False
    preserve_tree: bool = True
    source: EffectivePolicySource = EffectivePolicySource.SYSTEM_DEFAULT
    sidecar_path: str | None = None
    warnings: list[str] = Field(default_factory=list)


class TapeAssignment(BaseModel):
    """Files assigned to a single tape."""

    barcode: str
    files: list[str]
    estimated_bytes: int = 0
    is_spillover: bool = False
    shard_index: int | None = None


class ArchivePlanWarning(BaseModel):
    level: str
    message: str
    field: str | None = None


class ArchivePlan(BaseModel):
    """Dry-run output from the archive planner. No side effects."""

    plan_id: str = Field(default_factory=lambda: str(uuid4()))
    policy_name: str | None = None
    policy_type: PolicyType | None = None
    ingest_mode: IngestMode = IngestMode.CACHE_DRIVE
    source_path: str | None = None
    pool: str | None = None
    volume_group: str | None = None

    files: list[str] = Field(default_factory=list)
    total_files: int = 0
    total_bytes: int = 0

    tape_assignments: list[TapeAssignment] = Field(default_factory=list)
    estimated_tape_swaps: int = 0
    estimated_parallelism: int = 1

    copies_required: int = 1
    verify_before_archive: bool = True
    verify_after_archive: bool = True
    shard_size_bytes: int | None = None

    capacity_warnings: list[ArchivePlanWarning] = Field(default_factory=list)
    safety_warnings: list[ArchivePlanWarning] = Field(default_factory=list)
    manifest_strategy: str = "per_tape"

    is_safe_to_enqueue: bool = True
    enqueue_blockers: list[str] = Field(default_factory=list)

    created_at: str | None = None


class ArchivePlanRequest(BaseModel):
    """Input to the archive planner."""

    policy_id: str | None = None
    policy_type: PolicyType | None = None
    ingest_mode: IngestMode = IngestMode.CACHE_DRIVE
    source_path: str | None = None
    pool: str | None = None
    volume_group: str | None = None
    files: list[str] = Field(default_factory=list)
    file_sizes: dict[str, int] = Field(default_factory=dict)
    available_tapes: list[str] = Field(default_factory=list)
    tape_capacities: dict[str, int] = Field(default_factory=dict)
    copies: int = Field(default=1, ge=1, le=4)
    verify_before_archive: bool = True
    verify_after_archive: bool = True
    shard_size_bytes: int | None = Field(default=None, ge=1)
    shard_strategy: ShardStrategy | None = None
    max_parallelism: int = Field(default=1, ge=1, le=16)


class CacheDriveConfig(BaseModel):
    id: str
    name: str = Field(max_length=64)
    root_path: str
    max_bytes: int = Field(gt=0)
    min_free_bytes: int = Field(ge=0)
    eviction_policy: EvictionPolicy = EvictionPolicy.AFTER_VERIFIED
    retention_days: int = Field(default=30, ge=0)
    verify_before_archive: bool = True
    verify_after_archive: bool = True
    allow_source_delete_after_verify: bool = False
    stabilization_seconds: int = Field(default=5, ge=0)
    support_reflink_or_hardlink: bool = False
    quarantine_failed_files: bool = True
    quarantine_path: str | None = None
    enabled: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, value: object) -> str:
        return _strip_required_string(value, field_name="id")

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _strip_required_string(value, field_name="name", max_length=64)

    @field_validator("root_path", mode="before")
    @classmethod
    def validate_root_path(cls, value: object) -> str:
        root_path = _strip_required_string(value, field_name="root_path")
        if not root_path.startswith("/"):
            raise ValueError("root_path must start with '/'")
        return root_path

    @field_validator("quarantine_path", mode="before")
    @classmethod
    def validate_quarantine_path(cls, value: object) -> str | None:
        if value is None:
            return None
        quarantine_path = str(value).strip()
        if not quarantine_path:
            return None
        if not quarantine_path.startswith("/"):
            raise ValueError("quarantine_path must start with '/'")
        return quarantine_path


class SourceStreamConfig(BaseModel):
    enabled: bool = True
    require_source_online_for_entire_job: bool = True
    preflight_read_check: bool = True
    checksum_mode: Literal[
        "precompute", "streaming", "post_verify", "precompute_and_post_verify"
    ] = "precompute_and_post_verify"
    retry_policy: str = "linear"
    max_retries: int = Field(default=3, ge=0)
    fail_on_source_change: bool = True
    snapshot_required: bool = False
    source_change_detection: str = "size_mtime_checksum"
    allow_partial_dataset_success: bool = False


class NasPool(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    volume_group_ids: list[str] = Field(default_factory=list)
    default_policy_id: str | None = None
    default_ingest_mode: IngestMode = IngestMode.CACHE_DRIVE
    mount_path: str | None = None
    virtual_mount_enabled: bool = True
    hydration_behavior: HydrationBehavior = HydrationBehavior.QUEUE
    replication_factor: int = Field(default=1, ge=1, le=4)
    backup_order_mode: Literal["sequential", "parallel"] = "sequential"
    cache_target_id: str | None = None
    restore_target_path: str = "/openblade/restore"
    access_mode: PoolAccessMode = PoolAccessMode.READ_ONLY
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _strip_required_string(value, field_name="name", max_length=64)


class NasDataset(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    pool_id: str | None = None
    name: str = Field(..., min_length=1, max_length=128)
    source_path: str | None = None
    source_host: str | None = None
    policy_id: str | None = None
    ingest_mode: IngestMode | None = None
    volume_group_id: str | None = None
    tape_set: list[str] = Field(default_factory=list)
    shard_map: dict[str, list[str]] = Field(default_factory=dict)
    file_count: int = 0
    total_bytes: int = 0
    status: DatasetStatus = DatasetStatus.PENDING
    copies_completed: int = 0
    manifest_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _strip_required_string(value, field_name="name", max_length=128)


class NasFileState(str, Enum):
    ONLINE_CACHED = "online_cached"
    OFFLINE_ON_TAPE = "offline_on_tape"
    HYDRATING = "hydrating"
    MISSING_TAPE = "missing_tape"
    FAILED = "failed"
    CORRUPT = "corrupt"
    EXPORTED = "exported"


class NasFileRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    dataset_id: str
    pool_id: str | None = None
    relative_path: str = Field(..., min_length=1)
    source_path: str | None = None
    size_bytes: int = 0
    mtime: str | None = None
    checksum_sha256: str | None = None
    tape_barcode: str | None = None
    tape_offset: int | None = None
    status: NasFileState = NasFileState.OFFLINE_ON_TAPE
    cache_path: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class VirtualFileStatus(str, Enum):
    ONLINE_CACHED = "online_cached"
    OFFLINE_ON_TAPE = "offline_on_tape"
    HYDRATING = "hydrating"
    MISSING_TAPE = "missing_tape"
    FAILED = "failed"
    CORRUPT = "corrupt"
    EXPORTED = "exported"


class VirtualFileEntry(BaseModel):
    path: str
    name: str
    size_bytes: int
    mtime: str
    checksum_sha256: str = ""
    tape_barcode: str = ""
    status: VirtualFileStatus
    is_directory: bool = False
    pool: str = ""
    dataset_id: str = ""


class VirtualDirectoryListing(BaseModel):
    path: str
    entries: list[VirtualFileEntry]
    total_entries: int


class HydrationRequest(BaseModel):
    paths: list[str]
    pool: str = ""
    destination: str = "/openblade/restore"
    priority: int = Field(default=5, ge=1, le=10)
    allow_parallel: bool = True


class HydrationJob(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    paths: list[str]
    destination: str
    required_tapes: list[str]
    missing_tapes: list[str]
    total_files: int
    completed_files: int
    failed_files: int
    created_at: str
    updated_at: str
    error: str = ""


class RebuildRunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CatalogRebuildRunRecord(BaseModel):
    id: str
    status: RebuildRunStatus
    triggered_by: str
    barcodes_planned: list[str]
    barcodes_completed: list[str]
    barcodes_failed: list[str]
    barcodes_skipped: list[str]
    files_recovered: int = 0
    datasets_recovered: int = 0
    path_mappings_recovered: int = 0
    error_summary: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    completed_at: str | None = None


class ManifestVersionRecord(BaseModel):
    id: str
    barcode: str
    version_ts: str
    manifest_path: str
    sha256: str
    file_count: int = 0
    is_current: bool = False
    recorded_at: str


class RebuildPlanRequest(BaseModel):
    barcodes: list[str] = Field(default_factory=list, max_length=500)
    triggered_by: str = "system"
    dry_run: bool = False


class RebuildPlanResult(BaseModel):
    run_id: str
    dry_run: bool
    barcodes_to_scan: list[str] = Field(default_factory=list)
    barcodes_missing_manifest: list[str] = Field(default_factory=list)
    barcodes_missing_shard: list[str] = Field(default_factory=list)
    barcodes_invalid: list[str] = Field(default_factory=list)
    estimated_files: int = 0
    estimated_datasets: int = 0
    estimated_path_mappings: int = 0
    warnings: list[str] = Field(default_factory=list)
    safe_to_enqueue: bool


class RebuildActivationRequest(BaseModel):
    barcodes: list[str] = Field(default_factory=list, max_length=500)
    triggered_by: str = "operator"
    dry_run_first: bool = True


class RebuildActivationResult(BaseModel):
    run_id: str
    status: RebuildRunStatus
    files_recovered: int
    datasets_recovered: int
    path_mappings_recovered: int
    barcodes_completed: list[str]
    barcodes_failed: list[str]
    warnings: list[str]
    safe_to_enqueue: bool


class RestorePlanRequest(BaseModel):
    pool_id: str | None = None
    paths: list[str] = Field(default_factory=list)
    destination: str = "/openblade/restore"
    priority: int = 5
    allow_parallel: bool = True
    max_drives: int = 2


class RestoreJobStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NasRestoreJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    status: RestoreJobStatus = RestoreJobStatus.QUEUED
    priority: int = Field(default=5, ge=1, le=10)
    paths: list[str] = Field(default_factory=list)
    pool_id: str | None = None
    dataset_id: str | None = None
    destination: str = "/openblade/restore"
    allow_parallel: bool = True
    max_drives: int = Field(default=2, ge=1, le=8)
    cache_policy: str = "restore_to_destination"
    overwrite_policy: str = "skip_existing"
    required_tapes: list[str] = Field(default_factory=list)
    missing_tapes: list[str] = Field(default_factory=list)
    exported_tapes: list[str] = Field(default_factory=list)
    tape_load_order: list[str] = Field(default_factory=list)
    parallel_restore_groups: dict[str, list[str]] = Field(default_factory=dict)
    estimated_bytes: int = 0
    bytes_restored: int = 0
    files_restored: int = 0
    files_failed: int = 0
    partial_success: bool = False
    unavailable_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None


class ShareFolderMapping(BaseModel):
    folder_path: str
    pool_id: str
    access_mode: PoolAccessMode = PoolAccessMode.READ_ONLY

    @field_validator("folder_path", mode="before")
    @classmethod
    def validate_folder_path(cls, value: object) -> str:
        folder_path = _strip_required_string(value, field_name="folder_path")
        if not folder_path.startswith("/"):
            raise ValueError("folder_path must start with '/'")
        if ".." in folder_path.split("/"):
            raise ValueError("folder_path must not contain '..' components")
        return folder_path

    @field_validator("pool_id", mode="before")
    @classmethod
    def validate_pool_id(cls, value: object) -> str:
        return _strip_required_string(value, field_name="pool_id")


class NasShareDefinition(BaseModel):
    path: str
    name: str = Field(max_length=64)
    share_type: Literal["inbox", "restore", "catalog", "virtual", "pool"]
    default_policy_id: str | None = None
    pool_ids: list[str] = Field(default_factory=list)
    folder_mappings: list[ShareFolderMapping] = Field(default_factory=list)
    writable: bool = False
    description: str = ""

    @field_validator("path", mode="before")
    @classmethod
    def validate_path(cls, value: object) -> str:
        path = _strip_required_string(value, field_name="path")
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        return path

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: object) -> str:
        return _strip_required_string(value, field_name="name", max_length=64)

    @field_validator("default_policy_id", mode="before")
    @classmethod
    def validate_default_policy_id(cls, value: object) -> str | None:
        if value is None:
            return None
        return _strip_required_string(value, field_name="default_policy_id")

    @field_validator("pool_ids", mode="before")
    @classmethod
    def validate_pool_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("pool_ids must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            pool_id = _strip_required_string(item, field_name="pool_id")
            if pool_id in seen:
                continue
            seen.add(pool_id)
            normalized.append(pool_id)
        return normalized

    @model_validator(mode="after")
    def validate_folder_pool_membership(self) -> NasShareDefinition:
        if not self.pool_ids and self.folder_mappings:
            self.pool_ids = list(dict.fromkeys(item.pool_id for item in self.folder_mappings))
        if not self.folder_mappings:
            return self
        known = set(self.pool_ids)
        for mapping in self.folder_mappings:
            if mapping.pool_id not in known:
                raise ValueError(f"folder mapping references unknown pool_id {mapping.pool_id}")
        return self


class PathMappingRecord(BaseModel):
    """Logical-path → tape(s) mapping entry."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    logical_path: str = Field(..., min_length=1)
    pool_id: str = ""
    dataset_id: str = ""
    primary_barcode: str = ""
    all_barcodes: list[str] = Field(default_factory=list)
    file_record_id: str = ""
    file_state: NasFileState = NasFileState.OFFLINE_ON_TAPE
    restore_strategy: str = "single_tape"
    size: int = 0
    checksum: str = ""
    last_seen_at: str = ""
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    @field_validator("logical_path", mode="before")
    @classmethod
    def validate_logical_path(cls, v: object) -> str:
        """Reject empty paths and obvious traversal attempts."""
        s = str(v).strip()
        if not s:
            raise ValueError("logical_path must not be empty")
        if ".." in s.split("/"):
            raise ValueError("logical_path must not contain '..' components")
        return s


class PathLookupResult(BaseModel):
    """Result of a logical-path lookup."""

    logical_path: str
    found: bool
    pool_id: str = ""
    dataset_id: str = ""
    primary_barcode: str = ""
    all_barcodes: list[str] = Field(default_factory=list)
    file_state: NasFileState = NasFileState.OFFLINE_ON_TAPE
    restore_strategy: str = "single_tape"
    size: int = 0
    checksum: str = ""
    warnings: list[str] = Field(default_factory=list)


class PathMappingBulkUpsertRequest(BaseModel):
    entries: list[PathMappingRecord] = Field(..., max_length=1000)
    overwrite_existing: bool = True


class PathMappingSearchRequest(BaseModel):
    prefix: str = ""
    contains: str = ""
    pool_id: str = ""
    dataset_id: str = ""
    barcode: str = ""
    file_state: NasFileState | None = None
    limit: int = 200
    offset: int = 0
