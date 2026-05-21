import { ApiError, rootApiRequest } from './client';
import { listCartridges } from './media';

export interface CatalogRebuildPlan {
  run_id: string;
  dry_run: boolean;
  barcodes_to_scan: string[];
  barcodes_missing_manifest: string[];
  barcodes_missing_shard: string[];
  barcodes_invalid: string[];
  estimated_files: number;
  estimated_datasets: number;
  estimated_path_mappings: number;
  warnings: string[];
  safe_to_enqueue: boolean;
}

export interface CatalogRebuildRun {
  id: string;
  status: string;
  triggered_by: string;
  barcodes_planned: string[];
  barcodes_completed: string[];
  barcodes_failed: string[];
  barcodes_skipped: string[];
  files_recovered: number;
  datasets_recovered: number;
  path_mappings_recovered: number;
  error_summary: string[];
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface ManifestVersion {
  id: string;
  barcode: string;
  version_ts?: string;
  version_number?: number;
  manifest_path?: string;
  sha256: string;
  file_count?: number;
  is_current?: boolean;
  recorded_at?: string;
  written_at?: string;
  status?: 'valid' | 'corrupt';
  corrupt?: boolean;
}

function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort((left, right) => left.localeCompare(right));
}

function sortManifestVersions(records: ManifestVersion[]): ManifestVersion[] {
  const sorted = [...records].sort((left, right) => {
    const leftKey = left.version_ts ?? left.written_at ?? left.recorded_at ?? '';
    const rightKey = right.version_ts ?? right.written_at ?? right.recorded_at ?? '';
    return rightKey.localeCompare(leftKey) || left.barcode.localeCompare(right.barcode);
  });

  const versionNumbers = new Map<string, number>();
  return sorted.map((record) => {
    const nextVersion = (versionNumbers.get(record.barcode) ?? 0) + 1;
    versionNumbers.set(record.barcode, nextVersion);
    return {
      ...record,
      version_number: record.version_number ?? nextVersion,
      written_at: record.written_at ?? record.recorded_at ?? record.version_ts,
      status: record.status ?? (record.corrupt ? 'corrupt' : 'valid'),
    };
  });
}

export async function listLoadedRebuildTapes(): Promise<string[]> {
  return rootApiRequest<string[]>('/nas/catalog/rebuild/loaded-tapes');
}

export async function planCatalogRebuild(barcodes: string[]): Promise<CatalogRebuildPlan> {
  return rootApiRequest<CatalogRebuildPlan>('/nas/catalog/rebuild/plan', {
    method: 'POST',
    body: {
      barcodes,
      dry_run: true,
      triggered_by: 'operator',
    },
  });
}

export async function executeCatalogRebuild(runId: string): Promise<CatalogRebuildRun> {
  return rootApiRequest<CatalogRebuildRun>(`/nas/catalog/rebuild/${encodeURIComponent(runId)}/execute`, {
    method: 'POST',
  });
}

export async function listCatalogRebuildRuns(): Promise<CatalogRebuildRun[]> {
  return rootApiRequest<CatalogRebuildRun[]>('/nas/catalog/rebuild/runs');
}

export async function getCatalogRebuildRun(runId: string): Promise<CatalogRebuildRun> {
  return rootApiRequest<CatalogRebuildRun>(`/nas/catalog/rebuild/${encodeURIComponent(runId)}`);
}

export async function listManifestVersions(barcodeFilter = ''): Promise<ManifestVersion[]> {
  const barcode = barcodeFilter.trim().toUpperCase();
  const barcodes = barcode
    ? [barcode]
    : uniqueSorted((await listCartridges()).map((cartridge) => cartridge.barcode));

  const settled = await Promise.all(
    barcodes.map(async (item) => {
      try {
        return await rootApiRequest<ManifestVersion[]>(`/nas/catalog/manifest-versions/${encodeURIComponent(item)}`);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          return [];
        }
        throw error;
      }
    }),
  );

  return sortManifestVersions(settled.flat());
}

export interface PublicHealthResponse {
  status: 'ok' | 'degraded' | 'unhealthy' | string;
  components: Array<{
    name: string;
    status: 'ok' | 'degraded' | 'unhealthy' | string;
    message: string;
    latency_ms: number | null;
    last_checked_at: string;
  }>;
  checked_at: string;
  version: string;
}

export interface ReadyStatusResponse {
  ready: boolean;
  reason: string;
  checked_at: string;
}

export interface PublicVersionResponse {
  version: string;
  git_commit: string;
  build_date: string;
  python_version?: string;
  environment?: string;
}

export interface ErrorCodeEntry {
  code: string;
  severity: 'error' | 'warning' | 'info';
  title: string;
  description: string;
  action: string;
}

export interface LibraryDriveStatus {
  drive_id?: number;
  drive_index?: number;
  barcode?: string | null;
  loaded_barcode?: string | null;
  drive_state?: string;
  status?: string;
  mount_state?: string;
  loaded?: boolean;
  last_operation?: string;
}

export interface LibraryStatusResponse {
  library_connected: boolean;
  drives: LibraryDriveStatus[];
  slots_total: number;
  slots_occupied: number;
  cartridges_loaded: number;
  last_updated_at: string;
}

export interface CatalogStatusResponse {
  db_reachable: boolean;
  total_datasets: number;
  total_file_records: number;
  total_path_mappings: number;
  total_cartridges: number;
  last_rebuild_run_id: string | null;
  last_rebuild_status: string | null;
  checked_at: string;
}

export function getPublicHealth(): Promise<PublicHealthResponse> {
  return rootApiRequest<PublicHealthResponse>('/healthz');
}

export async function getSystemHealthDashboard(): Promise<{
  health: PublicHealthResponse;
  readiness: ReadyStatusResponse;
  version: PublicVersionResponse;
}> {
  const [health, readiness, version] = await Promise.all([
    getPublicHealth(),
    rootApiRequest<ReadyStatusResponse>('/readyz'),
    rootApiRequest<PublicVersionResponse>('/version'),
  ]);

  return { health, readiness, version };
}

export async function getErrorCodes(): Promise<ErrorCodeEntry[]> {
  const response = await rootApiRequest<{ error_codes: ErrorCodeEntry[] }>('/error-codes');
  return response.error_codes;
}

export async function getLibraryStatus(): Promise<LibraryStatusResponse> {
  return rootApiRequest<LibraryStatusResponse>('/status/library');
}

export async function getCatalogStatus(): Promise<CatalogStatusResponse> {
  return rootApiRequest<CatalogStatusResponse>('/status/catalog');
}
