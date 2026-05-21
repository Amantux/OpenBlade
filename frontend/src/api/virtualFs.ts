import { rootApiRequest } from './client';

export type VirtualFileStatus =
  | 'online_cached'
  | 'offline_on_tape'
  | 'hydrating'
  | 'missing_tape'
  | 'failed'
  | 'corrupt'
  | 'exported';

export interface VirtualFileEntry {
  path: string;
  name: string;
  size_bytes: number;
  mtime: string;
  checksum_sha256: string;
  tape_barcode: string;
  status: VirtualFileStatus;
  is_directory: boolean;
  pool: string;
  dataset_id: string;
}

export interface VirtualDirectoryListing {
  path: string;
  entries: VirtualFileEntry[];
  total_entries: number;
}

export interface HydrationJob {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  paths: string[];
  destination: string;
  required_tapes: string[];
  missing_tapes: string[];
  total_files: number;
  completed_files: number;
  failed_files: number;
  created_at: string;
  updated_at: string;
  error: string;
}

export interface HydrationRequest {
  paths: string[];
  pool: string;
  destination?: string;
  priority?: number;
  allow_parallel?: boolean;
}

export async function listVirtualDirectory(path: string): Promise<VirtualDirectoryListing> {
  const params = new URLSearchParams({ path });
  return rootApiRequest<VirtualDirectoryListing>(`/virtual/ls?${params.toString()}`);
}

export async function statVirtualPath(path: string): Promise<VirtualFileEntry> {
  const params = new URLSearchParams({ path });
  return rootApiRequest<VirtualFileEntry>(`/virtual/stat?${params.toString()}`);
}

export async function requestHydration(payload: HydrationRequest): Promise<HydrationJob> {
  return rootApiRequest<HydrationJob>('/virtual/hydrate', {
    method: 'POST',
    body: payload,
  });
}

export function listHydrationJobs(): Promise<HydrationJob[]> {
  return rootApiRequest<HydrationJob[]>('/virtual/jobs');
}

export async function cancelHydrationJob(jobId: string): Promise<HydrationJob> {
  return rootApiRequest<HydrationJob>(`/virtual/jobs/${encodeURIComponent(jobId)}`, {
    method: 'DELETE',
  });
}
