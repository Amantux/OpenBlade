import { useQuery } from '@tanstack/react-query';
import { cancelJob, getJob, getJobHistory, getJobs } from '../api/jobs';

export function useJobs(refetchInterval = 10_000) {
  return useQuery({
    queryKey: ['jobs'],
    queryFn: getJobs,
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useJobHistory(refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['jobs', 'history'],
    queryFn: getJobHistory,
    refetchInterval,
    refetchIntervalInBackground: false,
  });
}

export function useJob(jobId?: string, isActive = false) {
  return useQuery({
    queryKey: ['jobs', jobId],
    queryFn: () => getJob(jobId ?? ''),
    enabled: Boolean(jobId),
    refetchInterval: isActive ? 2_000 : 10_000,
    refetchIntervalInBackground: false,
  });
}

export { cancelJob };
