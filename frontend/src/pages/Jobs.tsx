import { useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listLibraries } from '../api/libraries';
import { cancelJob, listActiveJobs, listJobHistory } from '../api/operations';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import type { Job } from '../types/api';
import { useLibraryScope } from '../lib/useLibraryScope';
import { formatDate, formatDuration } from '../lib/utils';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (state) {
    case 'RUNNING':
      return 'blue';
    case 'COMPLETED':
    case 'SUCCESS':
      return 'green';
    case 'FAILED':
      return 'red';
    case 'PENDING':
      return 'amber';
    default:
      return 'gray';
  }
}

function StateBadge({ value }: { value: string }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${stateVariant(value) === 'blue' ? 'border-blue-500/30 bg-blue-500/15 text-blue-300' : stateVariant(value) === 'green' ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300' : stateVariant(value) === 'red' ? 'border-red-500/30 bg-red-500/15 text-red-300' : stateVariant(value) === 'amber' ? 'border-amber-500/30 bg-amber-500/15 text-amber-300' : 'border-slate-700 bg-slate-800 text-slate-200'}`}>{value}</span>;
}

function ProgressBar({ value }: { value: number }) {
  const progress = Math.max(0, Math.min(100, value));
  return (
    <div className="min-w-36">
      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full bg-quantum-red" style={{ width: `${progress}%` }} />
      </div>
      <div className="mt-1 text-xs text-slate-400">{progress}%</div>
    </div>
  );
}

function JobsTable({
  jobs,
  history,
  onCancel,
  cancellingId,
  librariesById,
}: {
  jobs: Job[];
  history?: boolean;
  onCancel?: (jobId: string) => void;
  cancellingId?: string;
  librariesById: Map<number, string>;
}) {
  return (
    <div className="overflow-x-auto rounded-md border border-quantum-border">
      <table className="min-w-full text-sm">
        <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
          <tr>
            <th className="px-4 py-3 font-medium">Job ID</th>
            <th className="px-4 py-3 font-medium">Type</th>
            <th className="px-4 py-3 font-medium">Library</th>
            <th className="px-4 py-3 font-medium">State</th>
            <th className="px-4 py-3 font-medium">Source</th>
            <th className="px-4 py-3 font-medium">Dest</th>
            <th className="px-4 py-3 font-medium">Progress</th>
            <th className="px-4 py-3 font-medium">Started</th>
            <th className="px-4 py-3 font-medium">Duration</th>
            {history ? <th className="px-4 py-3 font-medium">Completed</th> : null}
            {history ? <th className="px-4 py-3 font-medium">Result</th> : null}
            {!history ? <th className="px-4 py-3 font-medium">Actions</th> : null}
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 ? (
            <tr>
              <td colSpan={history ? 11 : 10} className="px-4 py-6 text-center text-slate-400">
                No {history ? 'historical' : 'active'} jobs available.
              </td>
            </tr>
          ) : (
            jobs.map((job, index) => {
              const cancelable = ['PENDING', 'RUNNING'].includes(job.state);
              const libraryLabel = job.library_id ? (librariesById.get(job.library_id) ?? `Library ${job.library_id}`) : 'Global';
              return (
                <tr key={job.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-200">{job.id}</td>
                  <td className="px-4 py-3 text-slate-300">{job.type}</td>
                  <td className="px-4 py-3 text-slate-300">{libraryLabel}</td>
                  <td className="px-4 py-3"><StateBadge value={job.state} /></td>
                  <td className="px-4 py-3 text-slate-300">{job.source ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-300">{job.destination ?? '—'}</td>
                  <td className="px-4 py-3"><ProgressBar value={job.progress} /></td>
                  <td className="px-4 py-3 text-slate-300">{formatDate(job.startedAt)}</td>
                  <td className="px-4 py-3 text-slate-300">{formatDuration(job.durationSeconds)}</td>
                  {history ? <td className="px-4 py-3 text-slate-300">{formatDate(job.completedAt ?? '')}</td> : null}
                  {history ? <td className="px-4 py-3"><StateBadge value={job.result ?? '—'} /></td> : null}
                  {!history ? (
                    <td className="px-4 py-3">
                      <Button
                        variant="danger"
                        className="px-3 py-1.5"
                        disabled={!cancelable || cancellingId === job.id}
                        onClick={() => onCancel?.(job.id)}
                      >
                        {cancellingId === job.id ? 'Cancelling…' : 'Cancel'}
                      </Button>
                    </td>
                  ) : null}
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

export default function Jobs() {
  const queryClient = useQueryClient();
  const { libraryId } = useLibraryScope();
  const [activeTab, setActiveTab] = useState<'active' | 'history'>('active');
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [libraryFilter, setLibraryFilter] = useState(() => libraryId || 'all');
  const previousLibraryId = useRef(libraryId);

  if (previousLibraryId.current !== libraryId) {
    previousLibraryId.current = libraryId;
    if (libraryId) {
      setLibraryFilter(libraryId);
    }
  }

  const activeJobsQuery = useQuery({
    queryKey: ['operations', 'jobs', 'active', libraryId],
    queryFn: listActiveJobs,
    refetchInterval: activeTab === 'active' && autoRefresh ? 5_000 : false,
  });
  const historyQuery = useQuery({
    queryKey: ['operations', 'jobs', 'history', libraryId],
    queryFn: listJobHistory,
    refetchInterval: activeTab === 'history' && autoRefresh ? 5_000 : false,
  });
  const librariesQuery = useQuery({
    queryKey: ['libraries'],
    queryFn: listLibraries,
    refetchInterval: 30_000,
  });

  const cancelMutation = useMutation({
    mutationFn: cancelJob,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['operations', 'jobs'] });
    },
  });

  const libraries = librariesQuery.data ?? [];
  const librariesById = useMemo(() => new Map(libraries.map((library) => [library.id, library.name])), [libraries]);
  const filterJobs = (jobs: Job[]) =>
    libraryFilter === 'all'
      ? jobs
      : jobs.filter((job) => job.library_id === null || String(job.library_id) === libraryFilter);
  const filteredActiveJobs = filterJobs(activeJobsQuery.data ?? []);
  const filteredHistoryJobs = filterJobs(historyQuery.data ?? []);

  const activeSummary = useMemo(() => ({
    running: filteredActiveJobs.filter((job) => job.state === 'RUNNING').length,
    pending: filteredActiveJobs.filter((job) => job.state === 'PENDING').length,
  }), [filteredActiveJobs]);

  if (activeJobsQuery.isLoading || historyQuery.isLoading || librariesQuery.isLoading) {
    return <Spinner />;
  }
  if (activeJobsQuery.isError) {
    return <ErrorMessage error={activeJobsQuery.error} onRetry={() => activeJobsQuery.refetch()} />;
  }
  if (historyQuery.isError) {
    return <ErrorMessage error={historyQuery.error} onRetry={() => historyQuery.refetch()} />;
  }
  if (librariesQuery.isError) {
    return <ErrorMessage error={librariesQuery.error} onRetry={() => librariesQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Operations Center</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">Jobs</h1>
            <p className="mt-2 text-sm text-slate-400">Track live AML queue activity and completed job history.</p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:min-w-[360px]">
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Running</div>
              <div className="mt-2 text-2xl font-semibold text-white">{activeSummary.running}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Queued</div>
              <div className="mt-2 text-2xl font-semibold text-white">{activeSummary.pending}</div>
            </div>
          </div>
        </div>
      </Card>

      <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-4 py-3 text-sm text-slate-300">
        <span className="font-medium text-blue-200">ℹ Demo mode:</span>{' '}
        Jobs labeled &quot;Global&quot; apply to all libraries. Library-specific jobs will appear once real library connections are established.
      </div>

      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="inline-flex rounded-md border border-quantum-border bg-quantum-sidebar p-1">
            <button type="button" className={`rounded px-4 py-2 text-sm font-semibold ${activeTab === 'active' ? 'bg-quantum-red text-white' : 'text-slate-300'}`} onClick={() => setActiveTab('active')}>
              Active Jobs
            </button>
            <button type="button" className={`rounded px-4 py-2 text-sm font-semibold ${activeTab === 'history' ? 'bg-quantum-red text-white' : 'text-slate-300'}`} onClick={() => setActiveTab('history')}>
              Job History
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm text-slate-300">
            <select
              value={libraryFilter}
              onChange={(event) => setLibraryFilter(event.target.value)}
              className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white"
            >
              <option value="all">All Libraries</option>
              {libraries.map((library) => (
                <option key={library.id} value={String(library.id)}>{library.name}</option>
              ))}
            </select>
            <label className="inline-flex items-center gap-2">
              <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
              Auto-refresh every 5s
            </label>
            <Button variant="secondary" onClick={() => void Promise.all([activeJobsQuery.refetch(), historyQuery.refetch(), librariesQuery.refetch()])}>
              Refresh now
            </Button>
          </div>
        </div>

        {cancelMutation.isError ? <div className="mt-4"><ErrorMessage error={cancelMutation.error} /></div> : null}

        <div className="mt-4">
          {activeTab === 'active' ? (
            <JobsTable
              jobs={filteredActiveJobs}
              onCancel={(jobId) => cancelMutation.mutate(jobId)}
              cancellingId={cancelMutation.variables}
              librariesById={librariesById}
            />
          ) : (
            <JobsTable jobs={filteredHistoryJobs} history librariesById={librariesById} />
          )}
        </div>
      </Card>
    </div>
  );
}
