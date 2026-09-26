import { rootApiRequest } from './client';
import type { CatalogListResponse } from '../types/api';

export interface TreeRestoreFailure {
  catalogPath: string;
  error: string;
}

export interface TreeRestoreResult {
  jobId: string;
  catalogPrefix: string;
  destDir: string;
  dryRun: boolean;
  filesRestored: number;
  filesFailed: number;
  bytesRestored: number;
  filesVerified: number;
  perTapeCounts: Record<string, number>;
  tapesUsed: string[];
  failures: TreeRestoreFailure[];
  /** Catalogued files with nothing archived: skipped, not failed, but named. */
  filesSkipped: number;
  skippedPaths: string[];
  status: string;
}

export interface TreeRestoreRequest {
  catalogPrefix: string;
  destDir: string;
  dryRun: boolean;
}

export function runTreeRestore(request: TreeRestoreRequest): Promise<TreeRestoreResult> {
  return rootApiRequest<TreeRestoreResult>('/restore/tree', {
    method: 'POST',
    body: {
      catalog_prefix: request.catalogPrefix,
      dest_dir: request.destDir,
      dry_run: request.dryRun,
    },
  });
}

/**
 * Catalog prefixes offered as starting points, derived from the same
 * `/catalog/` listing the Catalog Records page browses. The API has no
 * prefix/tree endpoint, so the directory levels are folded out of the file
 * paths here rather than invented server-side.
 */
export async function listCatalogPrefixes(limit = 500): Promise<string[]> {
  const params = new URLSearchParams({ limit: String(limit), offset: '0' });
  const response = await rootApiRequest<CatalogListResponse>(`/catalog/?${params.toString()}`);
  const prefixes = new Set<string>();
  for (const file of response.files) {
    const segments = file.source_path.split('/').filter(Boolean);
    // Every ancestor directory is a valid prefix; the file itself is not one.
    for (let depth = 1; depth < segments.length; depth += 1) {
      prefixes.add(`/${segments.slice(0, depth).join('/')}`);
    }
  }
  return [...prefixes].sort((left, right) => left.localeCompare(right));
}
