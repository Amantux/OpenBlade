import type {
  ArchiveRequestPayload,
  EnqueuedJobResponse,
  JobResponse,
  ShardedArchiveRequestPayload,
} from '../types/api';
import { rootApiRequest } from './client';

export function postArchive(payload: ArchiveRequestPayload): Promise<EnqueuedJobResponse> {
  return rootApiRequest<EnqueuedJobResponse>('/archive/', {
    method: 'POST',
    body: payload,
  });
}

export function postShardedArchive(
  payload: ShardedArchiveRequestPayload,
): Promise<EnqueuedJobResponse> {
  return rootApiRequest<EnqueuedJobResponse>('/archive/sharded', {
    method: 'POST',
    body: payload,
  });
}

export async function getArchiveJobs(): Promise<JobResponse[]> {
  const jobs = await rootApiRequest<JobResponse[]>('/jobs');
  return jobs.filter((job) => String(job.job_type ?? job.type ?? '').toLowerCase() === 'archive');
}
