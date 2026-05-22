import type { JobResponse } from '../types/api';
import { apiRequest } from './client';

interface AmlJobResource {
  id: string;
  type: string;
  status: string;
  priority: string;
  startTime: string;
  completedTime?: string | null;
  progress?: number;
  result?: string | null;
}

interface AmlJobListResponse {
  jobList: {
    job: AmlJobResource[];
  };
}

interface AmlJobResponse {
  job: AmlJobResource;
}

function normalizeJob(job: AmlJobResource): JobResponse {
  const updatedAt = job.completedTime ?? job.startTime;
  const status = String(job.status ?? 'unknown').toUpperCase();

  return {
    id: job.id,
    status,
    state: status,
    type: job.type,
    job_type: job.type,
    priority: job.priority,
    created_at: job.startTime,
    updated_at: updatedAt,
    progress: job.progress ?? undefined,
    result: job.result ?? null,
    error: status === 'FAILED' ? job.result ?? 'Job failed' : null,
    metadata: {},
    library_id: 1,
  };
}

export async function getJobs(): Promise<JobResponse[]> {
  const jobs = await apiRequest<AmlJobListResponse>('/jobs');
  return jobs.jobList.job.map(normalizeJob);
}

export async function getJobHistory(): Promise<JobResponse[]> {
  const jobs = await apiRequest<AmlJobListResponse>('/jobs/history');
  return jobs.jobList.job.map(normalizeJob);
}

export async function getJob(id: string): Promise<JobResponse> {
  const job = await apiRequest<AmlJobResponse>(`/job/${id}`);
  return normalizeJob(job.job);
}

export function cancelJob(id: string) {
  return apiRequest<{ summary: string }>(`/job/${id}`, {
    method: 'DELETE',
  });
}
