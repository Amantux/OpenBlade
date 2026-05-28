import { useQuery } from '@tanstack/react-query';
import { cancelJob, getJob, getJobHistory, getJobs } from '../api/jobs';

export function useJobs(libraryId = '', refetchInterval = 10_000) {
  return useQuery({
    queryKey: ['jobs', libraryId],
    queryFn: () => getJobs(libraryId),
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useJobHistory(libraryId = '', refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['jobs', 'history', libraryId],
    queryFn: () => getJobHistory(libraryId),
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useJob(jobId?: string, libraryId = '', isActive = false) {
  return useQuery({
    queryKey: ['jobs', libraryId, jobId],
    queryFn: () => getJob(jobId ?? '', libraryId),
    enabled: Boolean(jobId),
    refetchInterval: isActive ? 2_000 : 10_000,
    refetchIntervalInBackground: false,
  });
}

export { cancelJob };
