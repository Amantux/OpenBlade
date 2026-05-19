import { useEffect, useMemo, useState } from 'react';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useJob, useJobs } from '../hooks/useJobs';
import {
  getJobBarcode,
  getJobProgress,
  getJobShardText,
  getJobSourcePath,
  getJobState,
  getJobStrategy,
  getJobTypeLabel,
} from '../lib/lmc';
import { formatDate } from '../lib/utils';
import type { JobResponse } from '../types/api';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (state === 'FAILED') {
    return 'red';
  }
  if (state === 'RUNNING') {
    return 'blue';
  }
  if (state === 'COMPLETED') {
    return 'green';
  }
  return 'amber';
}

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
    Boolean(selectedSummary && ['PENDING', 'RUNNING'].includes(getJobState(selectedSummary))),
  );

  if (jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }

  const jobs = jobsQuery.data ?? [];
  const selectedJob = detailQuery.data ?? selectedSummary;

  return (
    <div className="space-y-4">
      <NorthPanel
        title="Active Jobs"
        subtitle="Archive and restore activity across all partitions."
        columns={[
          { key: 'id', header: 'Job ID', render: (row: JobResponse) => <span className="font-mono text-xs">{row.id}</span> },
          { key: 'type', header: 'Type', render: (row: JobResponse) => getJobTypeLabel(row) },
          {
            key: 'state',
            header: 'State',
            render: (row: JobResponse) => {
              const state = getJobState(row);
              return <Badge variant={stateVariant(state)}>{state}</Badge>;
            },
          },
          {
            key: 'progress',
            header: 'Progress',
            render: (row: JobResponse) => {
              const progress = Math.round(getJobProgress(row));
              return (
                <div className="min-w-40">
                  <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                    <div className="h-full bg-quantum-red" style={{ width: `${progress}%` }} />
                  </div>
                  <div className="mt-1 text-xs text-slate-400">{progress}%</div>
                </div>
              );
            },
          },
          { key: 'source', header: 'Source', render: (row: JobResponse) => getJobSourcePath(row) },
          { key: 'barcode', header: 'Barcode', render: (row: JobResponse) => getJobBarcode(row) },
          { key: 'started', header: 'Started', render: (row: JobResponse) => formatDate(row.created_at) },
        ]}
        rows={jobs}
        getRowId={(row) => row.id}
        selectedId={selectedJob?.id}
        onSelect={(row) => setSelectedJobId(row.id)}
        emptyMessage="No active jobs reported by the archive engine."
      />

      <InformationPanel
        title={selectedJob ? `Job ${selectedJob.id}` : 'Job Details'}
        subtitle="Expanded detail for the selected archive workflow."
        items={[
          { label: 'Type', value: selectedJob ? getJobTypeLabel(selectedJob) : '—' },
          { label: 'State', value: selectedJob ? getJobState(selectedJob) : '—' },
          { label: 'Progress', value: selectedJob ? `${Math.round(getJobProgress(selectedJob))}%` : '—' },
          { label: 'Source Path', value: selectedJob ? getJobSourcePath(selectedJob) : '—' },
          { label: 'Barcode', value: selectedJob ? getJobBarcode(selectedJob) : '—' },
          { label: 'Strategy', value: selectedJob ? getJobStrategy(selectedJob) : '—' },
          { label: 'Shards', value: selectedJob ? getJobShardText(selectedJob) : '—' },
          { label: 'Updated', value: selectedJob ? formatDate(selectedJob.updated_at) : '—' },
        ]}
      />

      <OperationsPanel
        title="Job Operations"
        subtitle="Cancel is available for selected jobs that are still queued or running."
        actions={[
          {
            label: 'Cancel',
            onClick: () => undefined,
            disabled: !selectedJob || !['PENDING', 'RUNNING'].includes(getJobState(selectedJob)),
            variant: 'danger',
          },
          { label: 'Refresh', onClick: () => void jobsQuery.refetch(), variant: 'secondary' },
        ]}
      />

      {detailQuery.isError ? (
        <ErrorMessage
          error={detailQuery.error}
          onRetry={() => detailQuery.refetch()}
          title="Unable to refresh selected job"
        />
      ) : null}
    </div>
  );
}
