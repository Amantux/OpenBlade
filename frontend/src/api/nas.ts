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

interface DeleteResponse {
  deleted: boolean;
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
