import { ApiError } from './client';
import { clearStoredUsername, notifyAuthRedirect } from '../lib/auth';

export type PolicyType = 'critical_sequential' | 'noncritical_sharded' | 'balanced';
export type IngestMode = 'cache_drive' | 'source_stream';
export type ShardStrategy = 'round_robin' | 'capacity_weighted' | 'directory_batch' | 'hash_prefix' | 'restore_parallelism_optimized';
export type EvictionPolicy = 'never' | 'after_verified' | 'after_days' | 'lru' | 'manual';
export type SourceStreamChecksumMode = 'precompute' | 'streaming' | 'post_verify' | 'precompute_and_post_verify';
export type SourceStreamRetryPolicy = 'none' | 'linear' | 'exponential';
export type EffectivePolicySource = 'system_default' | 'share_default' | 'sidecar';
export type NasShareType = 'inbox' | 'restore' | 'catalog' | 'virtual' | 'pool';
export type HydrationBehavior = 'queue' | 'auto' | 'manual';
export type PoolAccessMode = 'read_only' | 'read_write';

export interface StoragePolicy {
  id: string;
  name: string;
  policy_type: PolicyType;
  default_ingest_mode: IngestMode;
  copies_required: number;
  verify_before_archive: boolean;
  verify_after_archive: boolean;
  allow_spillover: boolean;
  allow_sharding: boolean;
  max_parallelism: number;
  shard_strategy: ShardStrategy | null;
  manifest_strategy: string;
  cache_retention: EvictionPolicy;
  allow_source_delete: boolean;
}

export interface PolicyInput {
  id?: string;
  name: string;
  policy_type: PolicyType;
  default_ingest_mode: IngestMode;
  copies_required: number;
  verify_before_archive: boolean;
  verify_after_archive: boolean;
  allow_spillover: boolean;
  allow_sharding: boolean;
  max_parallelism: number;
  shard_strategy?: ShardStrategy | null;
  manifest_strategy?: string;
  cache_retention?: EvictionPolicy;
  allow_source_delete?: boolean;
}

export interface CacheDriveConfig {
  id: string;
  name: string;
  root_path: string;
  max_bytes: number;
  min_free_bytes: number;
  eviction_policy: EvictionPolicy;
  retention_days: number;
  verify_before_archive: boolean;
  verify_after_archive: boolean;
  allow_source_delete_after_verify: boolean;
  stabilization_seconds: number;
  support_reflink_or_hardlink: boolean;
  quarantine_failed_files: boolean;
  quarantine_path: string | null;
  enabled: boolean;
}

export interface CacheDriveInput {
  id?: string;
  name: string;
  root_path: string;
  max_bytes: number;
  min_free_bytes: number;
  eviction_policy: EvictionPolicy;
  retention_days: number;
  verify_before_archive: boolean;
  verify_after_archive: boolean;
  allow_source_delete_after_verify?: boolean;
  stabilization_seconds?: number;
  support_reflink_or_hardlink?: boolean;
  quarantine_failed_files: boolean;
  quarantine_path?: string | null;
  enabled?: boolean;
}

export interface SourceStreamConfig {
  enabled: boolean;
  require_source_online_for_entire_job: boolean;
  preflight_read_check: boolean;
  checksum_mode: SourceStreamChecksumMode;
  retry_policy: SourceStreamRetryPolicy;
  max_retries: number;
  fail_on_source_change: boolean;
  snapshot_required: boolean;
  source_change_detection: string;
  allow_partial_dataset_success: boolean;
}

export interface SourceStreamInput {
  enabled: boolean;
  require_source_online_for_entire_job: boolean;
  preflight_read_check: boolean;
  checksum_mode: SourceStreamChecksumMode;
  retry_policy: SourceStreamRetryPolicy;
  max_retries: number;
  fail_on_source_change: boolean;
  snapshot_required: boolean;
  source_change_detection?: string;
  allow_partial_dataset_success: boolean;
}

export interface NasShareDefinition {
  path: string;
  name: string;
  share_type: NasShareType;
  default_policy_id: string | null;
  writable: boolean;
  description: string;
}

export interface ShareInput {
  path: string;
  name: string;
  share_type: NasShareType;
  default_policy_id?: string | null;
  writable?: boolean;
  description?: string;
}

export interface ResolvePolicyRequest {
  directory: string;
  share_id?: string | null;
}

export interface EffectivePolicy {
  policy_name: string | null;
  policy_id: string | null;
  ingest_mode: IngestMode;
  pool: string | null;
  volume_group: string | null;
  cache_drive: string | null;
  copies: number;
  verify_before_archive: boolean;
  verify_after_write: boolean;
  evict_cache_after_verified: boolean;
  preserve_tree: boolean;
  source: EffectivePolicySource;
  sidecar_path: string | null;
  warnings: string[];
}

export interface TapeAssignment {
  barcode: string;
  files: string[];
  estimated_bytes: number;
  is_spillover: boolean;
  shard_index: number | null;
}

export interface ArchivePlanWarning {
  level: string;
  message: string;
  field: string | null;
}

export interface ArchivePlan {
  plan_id: string;
  policy_name: string | null;
  policy_type: PolicyType | null;
  ingest_mode: IngestMode;
  source_path: string | null;
  pool: string | null;
  volume_group: string | null;
  files: string[];
  total_files: number;
  total_bytes: number;
  tape_assignments: TapeAssignment[];
  estimated_tape_swaps: number;
  estimated_parallelism: number;
  copies_required: number;
  verify_before_archive: boolean;
  verify_after_archive: boolean;
  capacity_warnings: ArchivePlanWarning[];
  safety_warnings: ArchivePlanWarning[];
  manifest_strategy: string;
  is_safe_to_enqueue: boolean;
  enqueue_blockers: string[];
  created_at: string | null;
}

export interface ArchivePlanRequest {
  policy_id?: string;
  policy_type?: PolicyType;
  ingest_mode?: IngestMode;
  source_path?: string;
  pool?: string;
  volume_group?: string;
  files: string[];
  file_sizes: Record<string, number>;
  available_tapes: string[];
  tape_capacities?: Record<string, number>;
  copies?: number;
  verify_before_archive?: boolean;
  verify_after_archive?: boolean;
  shard_strategy?: ShardStrategy | null;
  max_parallelism?: number;
}

export interface BrowseEntry {
  name: string;
  type: 'file' | 'directory';
  size_bytes: number;
  mtime: string | null;
  state: string | null;
  tape_barcode: string | null;
  checksum_sha256: string | null;
  logical_path: string;
}

export interface BrowseResult {
  pool_id: string;
  path: string;
  entries: BrowseEntry[];
  total_files: number;
  total_bytes: number;
  offline_count: number;
  online_count: number;
  hydrating_count: number;
}

export interface NasPool {
  pool_id: string;
  name: string;
  volume_groups: string[];
  default_policy_id: string | null;
  mount_path: string;
  virtual_mount_enabled: boolean;
  restore_target: string;
  access_mode: string;
  hydration_behavior: HydrationBehavior;
  cache_target: string | null;
  default_ingest_mode: IngestMode;
  description: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface NasPoolInput {
  pool_id?: string;
  name: string;
  volume_groups: string[];
  default_policy_id?: string | null;
  mount_path: string;
  virtual_mount_enabled: boolean;
  hydration_behavior: HydrationBehavior;
  cache_target?: string | null;
  restore_target: string;
  access_mode: PoolAccessMode;
  default_ingest_mode?: IngestMode;
  description?: string | null;
}

export interface NasFileRecord {
  id: string;
  dataset_id: string;
  pool_id: string | null;
  relative_path: string;
  source_path: string | null;
  size_bytes: number;
  mtime: string | null;
  checksum_sha256: string | null;
  tape_barcode: string | null;
  tape_offset: number | null;
  status: string;
  state: string;
  cache_path: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface RestorePlanRequest {
  pool_id?: string;
  paths: string[];
  destination: string;
  priority: number;
  allow_parallel: boolean;
  max_drives: number;
}

export interface RestorePlan {
  job_id: string;
  pool_id: string | null;
  requested_paths: string[];
  destination: string;
  priority: number;
  allow_parallel: boolean;
  max_drives: number;
  required_tapes: string[];
  missing_tapes: string[];
  exported_tapes: string[];
  tape_load_order: string[];
  batches_by_tape: Record<string, string[]>;
  parallel_restore_groups: string[][];
  estimated_tape_swaps: number;
  estimated_bytes: number;
  unavailable_files: string[];
  warnings: string[];
  is_safe_to_enqueue: boolean;
}

export interface NasRestoreJob {
  job_id: string;
  pool_id: string | null;
  dataset_id: string | null;
  status: string;
  required_tapes: string[];
  missing_tapes: string[];
  bytes_restored: number;
  files_restored: number;
  files_failed: number;
  errors: string[];
  destination: string;
  priority: number;
  partial_success: boolean;
  created_at: string | null;
  updated_at: string | null;
  completed_at: string | null;
  paths: string[];
  allow_parallel: boolean;
  max_drives: number;
  estimated_bytes: number;
  exported_tapes: string[];
  tape_load_order: string[];
  parallel_restore_groups: Record<string, string[]>;
  unavailable_files: string[];
  warnings: string[];
}

export interface NasDataset {
  dataset_id: string;
  pool_id: string | null;
  policy_id: string | null;
  status: string;
  ingest_mode: string;
  source_path: string | null;
  tape_set: string[];
  shard_map: Record<string, string[]>;
  file_count: number;
  total_bytes: number;
  copies_completed: number;
  name: string;
  policy_name: string | null;
  source_host: string | null;
  volume_group_id: string | null;
  manifest_path: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DatasetListFilters {
  status?: string;
  poolId?: string;
}

export interface DatasetManifestFile {
  logical_path: string;
  size_bytes: number;
  checksum_sha256: string | null;
  tape_barcode: string | null;
  state: string;
}

export interface DatasetManifest {
  dataset_id: string;
  policy_id: string | null;
  ingest_mode: string | null;
  tape_set: string[];
  shard_map: Record<string, string[]>;
  files: DatasetManifestFile[];
  total_files: number;
  total_bytes: number;
  generated_at: string;
}

export interface DatasetReport {
  dataset: NasDataset;
  files: NasFileRecord[];
  checksums: Record<string, string | null>;
  generated_at: string;
}

export interface DatasetVerificationResult {
  dataset_id: string;
  files_verified: number;
  files_corrupt: number;
  files_updated: number;
  checksums: Record<string, string>;
}

interface DeleteResponse {
  deleted: boolean;
}

interface RawNasPool {
  id?: string;
  pool_id?: string;
  name: string;
  volume_group_ids?: string[];
  volume_groups?: string[];
  default_policy_id?: string | null;
  mount_path?: string | null;
  virtual_mount_enabled?: boolean;
  hydration_behavior?: HydrationBehavior;
  cache_target_id?: string | null;
  cache_target?: string | null;
  restore_target_path?: string | null;
  restore_target?: string | null;
  access_mode?: string;
  default_ingest_mode?: IngestMode;
  description?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface RawNasFileRecord {
  id: string;
  dataset_id: string;
  pool_id?: string | null;
  relative_path: string;
  source_path?: string | null;
  size_bytes?: number;
  mtime?: string | null;
  checksum_sha256?: string | null;
  tape_barcode?: string | null;
  tape_offset?: number | null;
  status?: string;
  state?: string;
  cache_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface RawNasRestoreJob {
  id?: string;
  job_id?: string;
  pool_id?: string | null;
  dataset_id?: string | null;
  status?: string;
  required_tapes?: string[];
  missing_tapes?: string[];
  bytes_restored?: number;
  files_restored?: number;
  files_failed?: number;
  destination?: string;
  priority?: number;
  partial_success?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  paths?: string[];
  allow_parallel?: boolean;
  max_drives?: number;
  estimated_bytes?: number;
  exported_tapes?: string[];
  tape_load_order?: string[];
  parallel_restore_groups?: Record<string, string[]>;
  unavailable_files?: string[];
  warnings?: string[];
  errors?: string[];
  error_message?: string | null;
}

interface RawNasDataset {
  id?: string;
  dataset_id?: string;
  pool_id?: string | null;
  policy_id?: string | null;
  status?: string;
  ingest_mode?: string | null;
  source_path?: string | null;
  tape_set?: string[];
  shard_map?: Record<string, string[]>;
  file_count?: number;
  total_bytes?: number;
  copies_completed?: number;
  name?: string;
  policy_name?: string | null;
  source_host?: string | null;
  volume_group_id?: string | null;
  manifest_path?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

interface RawRestorePlan {
  job_id?: string;
  pool_id?: string | null;
  requested_paths?: string[];
  destination?: string;
  priority?: number;
  allow_parallel?: boolean;
  max_drives?: number;
  required_tapes?: string[];
  missing_tapes?: string[];
  exported_tapes?: string[];
  tape_load_order?: string[];
  batches_by_tape?: Record<string, string[]>;
  parallel_restore_groups?: string[][];
  estimated_tape_swaps?: number;
  estimated_bytes?: number;
  unavailable_files?: string[];
  warnings?: string[];
  is_safe_to_enqueue?: boolean;
}

interface RawDatasetManifest {
  dataset_id: string;
  policy_id?: string | null;
  ingest_mode?: string | null;
  tape_set?: string[];
  shard_map?: Record<string, string[]>;
  files?: DatasetManifestFile[];
  total_files?: number;
  total_bytes?: number;
  generated_at?: string;
}

interface RawDatasetReport {
  dataset: RawNasDataset;
  files?: RawNasFileRecord[];
  checksums?: Record<string, string | null>;
  generated_at?: string;
}

function redirectToLogin(): void {
  if (typeof window === 'undefined') {
    return;
  }

  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const redirect = encodeURIComponent(currentPath || '/');

  if (!window.location.pathname.startsWith('/login')) {
    clearStoredUsername();
    notifyAuthRedirect();
    window.location.assign(`/login?redirect=${redirect}`);
  }
}

function slugifyId(value: string): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return normalized || `nas-${Math.random().toString(36).slice(2, 10)}`;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function buildQueryString(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  });
  const query = search.toString();
  return query ? `?${query}` : '';
}

function mapPool(pool: RawNasPool): NasPool {
  return {
    pool_id: pool.pool_id ?? pool.id ?? '',
    name: pool.name,
    volume_groups: pool.volume_groups ?? pool.volume_group_ids ?? [],
    default_policy_id: pool.default_policy_id ?? null,
    mount_path: pool.mount_path ?? '',
    virtual_mount_enabled: pool.virtual_mount_enabled ?? true,
    restore_target: pool.restore_target ?? pool.restore_target_path ?? '/openblade/restore',
    access_mode: pool.access_mode ?? 'read_only',
    hydration_behavior: pool.hydration_behavior ?? 'queue',
    cache_target: pool.cache_target ?? pool.cache_target_id ?? null,
    default_ingest_mode: pool.default_ingest_mode ?? 'cache_drive',
    description: pool.description ?? null,
    created_at: pool.created_at ?? null,
    updated_at: pool.updated_at ?? null,
  };
}

function mapFileRecord(file: RawNasFileRecord): NasFileRecord {
  const state = file.state ?? file.status ?? 'offline_on_tape';
  return {
    id: file.id,
    dataset_id: file.dataset_id,
    pool_id: file.pool_id ?? null,
    relative_path: file.relative_path,
    source_path: file.source_path ?? null,
    size_bytes: file.size_bytes ?? 0,
    mtime: file.mtime ?? null,
    checksum_sha256: file.checksum_sha256 ?? null,
    tape_barcode: file.tape_barcode ?? null,
    tape_offset: file.tape_offset ?? null,
    status: state,
    state,
    cache_path: file.cache_path ?? null,
    created_at: file.created_at ?? null,
    updated_at: file.updated_at ?? null,
  };
}

function mapRestoreJob(job: RawNasRestoreJob): NasRestoreJob {
  const details = [...(job.errors ?? []), ...(job.error_message ? [job.error_message] : [])];
  return {
    job_id: job.job_id ?? job.id ?? '',
    pool_id: job.pool_id ?? null,
    dataset_id: job.dataset_id ?? null,
    status: job.status ?? 'queued',
    required_tapes: job.required_tapes ?? [],
    missing_tapes: job.missing_tapes ?? [],
    bytes_restored: job.bytes_restored ?? 0,
    files_restored: job.files_restored ?? 0,
    files_failed: job.files_failed ?? 0,
    errors: details,
    destination: job.destination ?? '/openblade/restore',
    priority: job.priority ?? 5,
    partial_success: job.partial_success ?? false,
    created_at: job.created_at ?? null,
    updated_at: job.updated_at ?? null,
    completed_at: job.completed_at ?? null,
    paths: job.paths ?? [],
    allow_parallel: job.allow_parallel ?? true,
    max_drives: job.max_drives ?? 2,
    estimated_bytes: job.estimated_bytes ?? 0,
    exported_tapes: job.exported_tapes ?? [],
    tape_load_order: job.tape_load_order ?? [],
    parallel_restore_groups: job.parallel_restore_groups ?? {},
    unavailable_files: job.unavailable_files ?? [],
    warnings: job.warnings ?? [],
  };
}

function mapDataset(dataset: RawNasDataset): NasDataset {
  return {
    dataset_id: dataset.dataset_id ?? dataset.id ?? '',
    pool_id: dataset.pool_id ?? null,
    policy_id: dataset.policy_id ?? null,
    status: dataset.status ?? 'pending',
    ingest_mode: dataset.ingest_mode ?? 'cache_drive',
    source_path: dataset.source_path ?? null,
    tape_set: dataset.tape_set ?? [],
    shard_map: dataset.shard_map ?? {},
    file_count: dataset.file_count ?? 0,
    total_bytes: dataset.total_bytes ?? 0,
    copies_completed: dataset.copies_completed ?? 0,
    name: dataset.name ?? dataset.dataset_id ?? dataset.id ?? 'Dataset',
    policy_name: dataset.policy_name ?? null,
    source_host: dataset.source_host ?? null,
    volume_group_id: dataset.volume_group_id ?? null,
    manifest_path: dataset.manifest_path ?? null,
    created_at: dataset.created_at ?? null,
    updated_at: dataset.updated_at ?? null,
  };
}

function mapRestorePlan(plan: RawRestorePlan): RestorePlan {
  return {
    job_id: plan.job_id ?? '',
    pool_id: plan.pool_id ?? null,
    requested_paths: plan.requested_paths ?? [],
    destination: plan.destination ?? '/openblade/restore',
    priority: plan.priority ?? 5,
    allow_parallel: plan.allow_parallel ?? true,
    max_drives: plan.max_drives ?? 2,
    required_tapes: plan.required_tapes ?? [],
    missing_tapes: plan.missing_tapes ?? [],
    exported_tapes: plan.exported_tapes ?? [],
    tape_load_order: plan.tape_load_order ?? [],
    batches_by_tape: plan.batches_by_tape ?? {},
    parallel_restore_groups: plan.parallel_restore_groups ?? [],
    estimated_tape_swaps: plan.estimated_tape_swaps ?? 0,
    estimated_bytes: plan.estimated_bytes ?? 0,
    unavailable_files: plan.unavailable_files ?? [],
    warnings: plan.warnings ?? [],
    is_safe_to_enqueue: plan.is_safe_to_enqueue ?? true,
  };
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const payload = text ? safeJsonParse(text) : null;

  if (response.status === 401) {
    redirectToLogin();
  }

  if (!response.ok) {
    const details = typeof payload === 'object' && payload !== null ? JSON.stringify(payload, null, 2) : text;
    const message =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String(payload.detail)
        : `Request failed with status ${response.status}`;
    throw new ApiError(
      message,
      response.status,
      'The NAS backend request could not be completed.',
      response.status === 401 ? 'Sign in again to continue.' : 'Review the request details and retry.',
      details,
    );
  }

  return payload as T;
}

async function nasRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`/api/nas${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });

  return parseResponse<T>(response);
}

function normalizePolicyPayload(data: PolicyInput, id?: string): StoragePolicy {
  return {
    id: id ?? data.id ?? slugifyId(data.name),
    name: data.name.trim(),
    policy_type: data.policy_type,
    default_ingest_mode: data.default_ingest_mode,
    copies_required: data.copies_required,
    verify_before_archive: data.verify_before_archive,
    verify_after_archive: data.verify_after_archive,
    allow_spillover: data.allow_spillover,
    allow_sharding: data.allow_sharding,
    max_parallelism: data.max_parallelism,
    shard_strategy: data.allow_sharding ? data.shard_strategy ?? 'round_robin' : null,
    manifest_strategy: data.manifest_strategy ?? 'per_tape',
    cache_retention: data.cache_retention ?? 'after_verified',
    allow_source_delete: data.allow_source_delete ?? false,
  };
}

function normalizeCacheDrivePayload(data: CacheDriveInput, id?: string): CacheDriveConfig {
  return {
    id: id ?? data.id ?? slugifyId(data.name),
    name: data.name.trim(),
    root_path: data.root_path.trim(),
    max_bytes: data.max_bytes,
    min_free_bytes: data.min_free_bytes,
    eviction_policy: data.eviction_policy,
    retention_days: data.eviction_policy === 'after_days' ? data.retention_days : data.retention_days,
    verify_before_archive: data.verify_before_archive,
    verify_after_archive: data.verify_after_archive,
    allow_source_delete_after_verify: data.allow_source_delete_after_verify ?? false,
    stabilization_seconds: data.stabilization_seconds ?? 5,
    support_reflink_or_hardlink: data.support_reflink_or_hardlink ?? false,
    quarantine_failed_files: data.quarantine_failed_files,
    quarantine_path: data.quarantine_path?.trim() || null,
    enabled: data.enabled ?? true,
  };
}

function normalizeSourceStreamPayload(data: SourceStreamInput): SourceStreamConfig {
  return {
    enabled: data.enabled,
    require_source_online_for_entire_job: data.require_source_online_for_entire_job,
    preflight_read_check: data.preflight_read_check,
    checksum_mode: data.checksum_mode,
    retry_policy: data.retry_policy,
    max_retries: data.max_retries,
    fail_on_source_change: data.fail_on_source_change,
    snapshot_required: data.snapshot_required,
    source_change_detection: data.source_change_detection ?? 'size_mtime_checksum',
    allow_partial_dataset_success: data.allow_partial_dataset_success,
  };
}

function normalizePoolPayload(data: NasPoolInput): Record<string, unknown> {
  return {
    id: data.pool_id,
    name: data.name.trim(),
    volume_group_ids: data.volume_groups,
    default_policy_id: data.default_policy_id ?? null,
    mount_path: data.mount_path.trim(),
    virtual_mount_enabled: data.virtual_mount_enabled,
    hydration_behavior: data.hydration_behavior,
    cache_target_id: data.cache_target?.trim() || null,
    restore_target_path: data.restore_target.trim(),
    access_mode: data.access_mode,
    default_ingest_mode: data.default_ingest_mode ?? 'cache_drive',
    description: data.description?.trim() || null,
  };
}

export function listPolicies(): Promise<StoragePolicy[]> {
  return nasRequest<StoragePolicy[]>('/policies');
}

export function createPolicy(data: PolicyInput): Promise<StoragePolicy> {
  return nasRequest<StoragePolicy>('/policies', {
    method: 'POST',
    body: JSON.stringify(normalizePolicyPayload(data)),
  });
}

// Backend policy updates are implemented as POST upserts.
export function updatePolicy(id: string, data: PolicyInput): Promise<StoragePolicy> {
  return nasRequest<StoragePolicy>('/policies', {
    method: 'POST',
    body: JSON.stringify(normalizePolicyPayload(data, id)),
  });
}

export async function deletePolicy(id: string): Promise<void> {
  await nasRequest<DeleteResponse>(`/policies/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function listCacheDrives(): Promise<CacheDriveConfig[]> {
  return nasRequest<CacheDriveConfig[]>('/cache-drives');
}

export function createCacheDrive(data: CacheDriveInput): Promise<CacheDriveConfig> {
  return nasRequest<CacheDriveConfig>('/cache-drives', {
    method: 'POST',
    body: JSON.stringify(normalizeCacheDrivePayload(data)),
  });
}

// Backend cache drive updates are implemented as POST upserts.
export function updateCacheDrive(id: string, data: CacheDriveInput): Promise<CacheDriveConfig> {
  return nasRequest<CacheDriveConfig>('/cache-drives', {
    method: 'POST',
    body: JSON.stringify(normalizeCacheDrivePayload(data, id)),
  });
}

export async function deleteCacheDrive(id: string): Promise<void> {
  await nasRequest<DeleteResponse>(`/cache-drives/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function getSourceStream(): Promise<SourceStreamConfig> {
  return nasRequest<SourceStreamConfig>('/source-stream');
}

export function updateSourceStream(data: SourceStreamInput): Promise<SourceStreamConfig> {
  return nasRequest<SourceStreamConfig>('/source-stream', {
    method: 'PUT',
    body: JSON.stringify(normalizeSourceStreamPayload(data)),
  });
}

export function listShares(): Promise<NasShareDefinition[]> {
  return nasRequest<NasShareDefinition[]>('/shares');
}

export function createShare(data: ShareInput): Promise<NasShareDefinition> {
  return nasRequest<NasShareDefinition>('/shares', {
    method: 'POST',
    body: JSON.stringify({
      path: data.path,
      name: data.name,
      share_type: data.share_type,
      default_policy_id: data.default_policy_id ?? null,
      writable: data.writable ?? false,
      description: data.description ?? '',
    }),
  });
}

export function listPools(): Promise<NasPool[]> {
  return nasRequest<RawNasPool[]>('/pools').then((pools) => pools.map(mapPool));
}

export function getPool(id: string): Promise<NasPool> {
  return nasRequest<RawNasPool>(`/pools/${encodeURIComponent(id)}`).then(mapPool);
}

export function createPool(data: NasPoolInput): Promise<NasPool> {
  return nasRequest<RawNasPool>('/pools', {
    method: 'POST',
    body: JSON.stringify(normalizePoolPayload(data)),
  }).then(mapPool);
}

export function updatePool(id: string, data: NasPoolInput): Promise<NasPool> {
  return nasRequest<RawNasPool>(`/pools/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(normalizePoolPayload(data)),
  }).then(mapPool);
}

export async function deletePool(id: string): Promise<void> {
  await nasRequest<void>(`/pools/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export function browsePool(id: string, path = ''): Promise<BrowseResult> {
  return nasRequest<BrowseResult>(`/pools/${encodeURIComponent(id)}/browse${buildQueryString({ path })}`);
}

export function getPoolFile(id: string, path: string): Promise<NasFileRecord> {
  return nasRequest<RawNasFileRecord>(`/pools/${encodeURIComponent(id)}/files/${encodeURIComponent(path)}`).then(mapFileRecord);
}

export function createRestorePlan(body: RestorePlanRequest): Promise<RestorePlan> {
  return nasRequest<RawRestorePlan>('/restore-plan', {
    method: 'POST',
    body: JSON.stringify(body),
  }).then(mapRestorePlan);
}

export function requestRestore(poolId: string, body: RestorePlanRequest): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/pools/${encodeURIComponent(poolId)}/request-restore`, {
    method: 'POST',
    body: JSON.stringify(body),
  }).then(mapRestoreJob);
}

export function listRestoreJobs(): Promise<NasRestoreJob[]> {
  return nasRequest<RawNasRestoreJob[]>('/restore-jobs').then((jobs) => jobs.map(mapRestoreJob));
}

export function getRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}`).then(mapRestoreJob);
}

export function runRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}/run`, { method: 'POST' }).then(mapRestoreJob);
}

export function cancelRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }).then(mapRestoreJob);
}

export function pauseRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}/pause`, { method: 'POST' }).then(mapRestoreJob);
}

export function resumeRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}/resume`, { method: 'POST' }).then(mapRestoreJob);
}

export function retryRestoreJob(id: string): Promise<NasRestoreJob> {
  return nasRequest<RawNasRestoreJob>(`/restore-jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }).then(mapRestoreJob);
}

export function listDatasets(filters: DatasetListFilters = {}): Promise<NasDataset[]> {
  return nasRequest<RawNasDataset[]>(`/datasets${buildQueryString({ pool_id: filters.poolId, status: filters.status })}`).then((datasets) => datasets.map(mapDataset));
}

export function getDataset(id: string): Promise<NasDataset> {
  return nasRequest<RawNasDataset>(`/datasets/${encodeURIComponent(id)}`).then(mapDataset);
}

export function getDatasetFiles(id: string, skip = 0, limit = 1000): Promise<NasFileRecord[]> {
  return nasRequest<RawNasFileRecord[]>(`/datasets/${encodeURIComponent(id)}/files${buildQueryString({ skip, limit })}`).then((files) => files.map(mapFileRecord));
}

export function getDatasetManifest(id: string): Promise<DatasetManifest> {
  return nasRequest<RawDatasetManifest>(`/datasets/${encodeURIComponent(id)}/manifest`).then((manifest) => ({
    dataset_id: manifest.dataset_id,
    policy_id: manifest.policy_id ?? null,
    ingest_mode: manifest.ingest_mode ?? null,
    tape_set: manifest.tape_set ?? [],
    shard_map: manifest.shard_map ?? {},
    files: manifest.files ?? [],
    total_files: manifest.total_files ?? 0,
    total_bytes: manifest.total_bytes ?? 0,
    generated_at: manifest.generated_at ?? '',
  }));
}

export function getDatasetReport(id: string): Promise<DatasetReport> {
  return nasRequest<RawDatasetReport>(`/datasets/${encodeURIComponent(id)}/report`).then((report) => ({
    dataset: mapDataset(report.dataset),
    files: (report.files ?? []).map(mapFileRecord),
    checksums: report.checksums ?? {},
    generated_at: report.generated_at ?? '',
  }));
}

export function verifyDataset(id: string): Promise<DatasetVerificationResult> {
  return nasRequest<DatasetVerificationResult>(`/datasets/${encodeURIComponent(id)}/verify`, { method: 'POST' });
}

export function exportDataset(id: string): Promise<NasDataset> {
  return nasRequest<RawNasDataset>(`/datasets/${encodeURIComponent(id)}/export`, { method: 'POST' }).then(mapDataset);
}

export function resolvePolicy(data: ResolvePolicyRequest): Promise<EffectivePolicy> {
  return nasRequest<EffectivePolicy>('/resolve-policy', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function createArchivePlan(data: ArchivePlanRequest): Promise<ArchivePlan> {
  return nasRequest<ArchivePlan>('/archive-plan', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
