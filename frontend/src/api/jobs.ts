import type { JobResponse } from '../types/api';
import { apiRequest } from './client';

interface RawJobResponse {
  id: string;
  state?: string;
  status?: string;
  job_type: string;
  created_at?: string;
  updated_at?: string;
  error?: string | null;
  metadata?: Record<string, unknown>;
}

function getString(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function getNumber(value: unknown): number | undefined {
  return typeof value === 'number' ? value : undefined;
}

function normalizeJob(job: RawJobResponse): JobResponse {
  const metadata = job.metadata ?? {};
  return {
    id: job.id,
    status: String(job.status ?? job.state ?? 'unknown').toUpperCase(),
    job_type: job.job_type,
    created_at: getString(job.created_at ?? metadata.created_at),
    updated_at: getString(job.updated_at ?? metadata.updated_at),
    error: job.error ?? null,
    metadata,
    bytes_written: getNumber(metadata.bytes_written),
  };
}

export async function getJobs(): Promise<JobResponse[]> {
  const jobs = await apiRequest<RawJobResponse[]>('/jobs/');
  return jobs.map(normalizeJob);
}

export async function getJob(id: string): Promise<JobResponse> {
  const job = await apiRequest<RawJobResponse>(`/jobs/${id}`);
  return normalizeJob(job);
}
