import { useEffect, useMemo, useState } from 'react';
import JobCard from '../components/jobs/JobCard';
import JobList from '../components/jobs/JobList';
import JobProgress from '../components/jobs/JobProgress';
import ErrorMessage from '../components/ui/ErrorMessage';
import Card from '../components/ui/Card';
import Spinner from '../components/ui/Spinner';
import { useJob, useJobs } from '../hooks/useJobs';

export default function Jobs() {
  const jobsQuery = useJobs();
  const [selectedJobId, setSelectedJobId] = useState<string>();

  useEffect(() => {
    if (!selectedJobId && jobsQuery.data && jobsQuery.data.length > 0) {
      setSelectedJobId(jobsQuery.data[0].id);
    }
  }, [jobsQuery.data, selectedJobId]);

  const selectedSummary = useMemo(
    () => jobsQuery.data?.find((job) => job.id === selectedJobId),
    [jobsQuery.data, selectedJobId],
  );
  const detailQuery = useJob(
    selectedJobId,
    Boolean(selectedSummary && ['PENDING', 'RUNNING'].includes(selectedSummary.status)),
  );

  if (jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }

  const jobs = jobsQuery.data ?? [];
  const detailJob = detailQuery.data ?? selectedSummary;

  return (
    <div className="grid gap-6 xl:grid-cols-[1.1fr,1fr]">
      <JobList jobs={jobs} selectedId={selectedJobId} onSelect={setSelectedJobId} />
      <div className="space-y-6">
        {detailJob ? (
          <>
            <JobCard job={detailJob} />
            <JobProgress job={detailJob} />
            <Card>
              <h3 className="text-lg font-semibold text-white">Job metadata</h3>
              <div className="mt-4 overflow-hidden rounded-xl border border-slate-800">
                <table className="min-w-full divide-y divide-slate-800 text-sm">
                  <tbody className="divide-y divide-slate-800">
                    {Object.entries(detailJob.metadata ?? {}).length > 0 ? (
                      Object.entries(detailJob.metadata ?? {}).map(([key, value]) => (
                        <tr key={key}>
                          <td className="px-4 py-3 font-medium text-slate-300">{key}</td>
                          <td className="px-4 py-3 text-slate-400">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td className="px-4 py-6 text-slate-400">No metadata available.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        ) : (
          <Card className="text-sm text-slate-400">Select a job to inspect details.</Card>
        )}
        {detailQuery.isError ? <ErrorMessage error={detailQuery.error} onRetry={() => detailQuery.refetch()} title="Unable to refresh selected job" /> : null}
      </div>
    </div>
  );
}
