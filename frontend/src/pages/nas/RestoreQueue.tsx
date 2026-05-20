import { Fragment, useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  cancelRestoreJob,
  createRestorePlan,
  listPools,
  listRestoreJobs,
  pauseRestoreJob,
  requestRestore,
  resumeRestoreJob,
  retryRestoreJob,
  runRestoreJob,
  type NasRestoreJob,
  type RestorePlan,
  type RestorePlanRequest,
} from '../../api/nas';
import BytesDisplay from '../../components/nas/BytesDisplay';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { formatDate } from '../../lib/utils';

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

interface QuickRestoreForm {
  pool_id: string;
  destination: string;
  priority: number;
  allow_parallel: boolean;
  max_drives: number;
}

const defaultForm: QuickRestoreForm = {
  pool_id: '',
  destination: '/openblade/restore',
  priority: 5,
  allow_parallel: true,
  max_drives: 2,
};

function shortId(value: string): string {
  return value.length > 8 ? value.slice(0, 8) : value;
}

function actionLabel(status: string): string | null {
  switch (status.toUpperCase()) {
    case 'QUEUED':
      return 'Run';
    case 'RUNNING':
      return 'Pause';
    case 'PAUSED':
      return 'Resume';
    case 'FAILED':
      return 'Retry';
    default:
      return null;
  }
}

function estimatedTotalFiles(job: NasRestoreJob): number {
  return Math.max(job.paths.length, job.files_restored + job.files_failed, job.required_tapes.length, 1);
}

export default function RestoreQueue() {
  const queryClient = useQueryClient();
  const jobsQuery = useQuery({
    queryKey: ['nas', 'restore-jobs'],
    queryFn: listRestoreJobs,
    refetchInterval: 5000,
  });
  const poolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listPools });

  const [expandedJobId, setExpandedJobId] = useState<string | null>(null);
  const [isQuickFormOpen, setIsQuickFormOpen] = useState(false);
  const [form, setForm] = useState<QuickRestoreForm>(defaultForm);
  const [plan, setPlan] = useState<RestorePlan | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    if (!form.pool_id && poolsQuery.data?.[0]) {
      setForm((current) => ({ ...current, pool_id: poolsQuery.data?.[0]?.pool_id ?? '' }));
    }
  }, [form.pool_id, poolsQuery.data]);

  const poolNameMap = useMemo(
    () => new Map((poolsQuery.data ?? []).map((pool) => [pool.pool_id, pool.name])),
    [poolsQuery.data],
  );

  const requestBody: RestorePlanRequest = {
    pool_id: form.pool_id || undefined,
    paths: [],
    destination: form.destination,
    priority: form.priority,
    allow_parallel: form.allow_parallel,
    max_drives: form.max_drives,
  };

  const planMutation = useMutation({
    mutationFn: () => createRestorePlan(requestBody),
    onSuccess: (result) => {
      setPlan(result);
      setToast(null);
    },
    onError: (error) => {
      setPlan(null);
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to plan restore.' });
    },
  });

  const enqueueMutation = useMutation({
    mutationFn: () => {
      if (!form.pool_id) {
        throw new Error('Select a pool before enqueueing.');
      }
      return requestRestore(form.pool_id, requestBody);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'restore-jobs'] });
      setToast({ type: 'success', message: 'Restore job enqueued.' });
      setPlan(null);
      setIsQuickFormOpen(false);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to enqueue restore.' });
    },
  });

  const jobActionMutation = useMutation({
    mutationFn: async ({ jobId, action }: { jobId: string; action: 'run' | 'pause' | 'resume' | 'retry' | 'cancel' }) => {
      switch (action) {
        case 'run':
          return runRestoreJob(jobId);
        case 'pause':
          return pauseRestoreJob(jobId);
        case 'resume':
          return resumeRestoreJob(jobId);
        case 'retry':
          return retryRestoreJob(jobId);
        case 'cancel':
          return cancelRestoreJob(jobId);
      }
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'restore-jobs'] });
      setToast({ type: 'success', message: 'Restore job updated.' });
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to update restore job.' });
    },
  });

  if (jobsQuery.isLoading || poolsQuery.isLoading) {
    return <Spinner />;
  }

  if (jobsQuery.isError || poolsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error ?? poolsQuery.error} onRetry={() => {
      void jobsQuery.refetch();
      void poolsQuery.refetch();
    }} />;
  }

  const jobs = jobsQuery.data ?? [];

  return (
    <div className="space-y-4">
      {toast ? (
        <div className={`fixed right-4 top-4 z-50 rounded-md border px-4 py-3 text-sm shadow-lg ${toast.type === 'success' ? 'border-emerald-500/30 bg-emerald-900/90 text-emerald-100' : 'border-red-500/30 bg-red-950/90 text-red-100'}`}>
          {toast.message}
        </div>
      ) : null}

      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Restore Queue</h1>
            <p className="mt-2 text-sm text-slate-400">Monitor queued and active restore jobs with automatic 5-second refresh.</p>
          </div>
          <Button type="button" onClick={() => setIsQuickFormOpen((current) => !current)}>
            New Restore
          </Button>
        </div>
      </Card>

      {isQuickFormOpen ? (
        <Card>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Pool</span>
              <select value={form.pool_id} onChange={(event) => setForm((current) => ({ ...current, pool_id: event.target.value }))}>
                <option value="">Select a pool</option>
                {(poolsQuery.data ?? []).map((pool) => (
                  <option key={pool.pool_id} value={pool.pool_id}>{pool.name}</option>
                ))}
              </select>
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Destination</span>
              <input value={form.destination} onChange={(event) => setForm((current) => ({ ...current, destination: event.target.value }))} />
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Priority</span>
              <input type="number" min={1} max={10} value={form.priority} onChange={(event) => setForm((current) => ({ ...current, priority: Math.min(10, Math.max(1, Number(event.target.value) || 1)) }))} />
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Max drives</span>
              <input type="number" min={1} max={8} value={form.max_drives} onChange={(event) => setForm((current) => ({ ...current, max_drives: Math.min(8, Math.max(1, Number(event.target.value) || 1)) }))} />
            </label>
            <label className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200 md:col-span-2 xl:col-span-4">
              <span>Allow parallel tape restores</span>
              <input type="checkbox" checked={form.allow_parallel} onChange={(event) => setForm((current) => ({ ...current, allow_parallel: event.target.checked }))} />
            </label>
          </div>

          <div className="mt-4 flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={!form.pool_id || planMutation.isPending || enqueueMutation.isPending} onClick={() => planMutation.mutate()}>
              {planMutation.isPending ? 'Planning…' : 'Plan Restore'}
            </Button>
            <Button type="button" disabled={!form.pool_id || enqueueMutation.isPending} onClick={() => enqueueMutation.mutate()}>
              {enqueueMutation.isPending ? 'Enqueueing…' : 'Enqueue'}
            </Button>
          </div>

          {plan ? (
            <div className="mt-4 rounded-lg border border-quantum-border bg-quantum-sidebar p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Restore summary</div>
                  <div className="mt-2 text-sm text-slate-100">{plan.required_tapes.length} tape(s), {plan.requested_paths.length || 'all'} file selection</div>
                </div>
                <NasStatusBadge value={plan.is_safe_to_enqueue ? 'completed' : 'failed'} label={plan.is_safe_to_enqueue ? 'Ready' : 'Review warnings'} />
              </div>
              <div className="mt-4 grid gap-3 md:grid-cols-4">
                <div className="rounded-md border border-quantum-border px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Required tapes</div>
                  <div className="mt-2 text-sm text-slate-100">{plan.required_tapes.length}</div>
                </div>
                <div className="rounded-md border border-quantum-border px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Missing tapes</div>
                  <div className="mt-2 text-sm text-slate-100">{plan.missing_tapes.length}</div>
                </div>
                <div className="rounded-md border border-quantum-border px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Estimated bytes</div>
                  <div className="mt-2"><BytesDisplay value={plan.estimated_bytes} /></div>
                </div>
                <div className="rounded-md border border-quantum-border px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape swaps</div>
                  <div className="mt-2 text-sm text-slate-100">{plan.estimated_tape_swaps}</div>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                {plan.required_tapes.map((tape) => (
                  <span key={tape} className="rounded-full border border-quantum-border px-2.5 py-1 text-xs text-slate-200">{tape}</span>
                ))}
              </div>
              {plan.warnings.length > 0 ? (
                <div className="mt-4 space-y-2">
                  {plan.warnings.map((warning) => (
                    <div key={warning} className="rounded-md border border-amber-500/30 bg-amber-900/20 px-3 py-2 text-sm text-amber-100">{warning}</div>
                  ))}
                </div>
              ) : null}
            </div>
          ) : null}
        </Card>
      ) : null}

      <Card>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Job ID</th>
                <th className="px-3 py-3">Pool</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Required Tapes</th>
                <th className="px-3 py-3">Missing Tapes</th>
                <th className="px-3 py-3">Destination</th>
                <th className="px-3 py-3">Progress</th>
                <th className="px-3 py-3">Bytes Restored</th>
                <th className="px-3 py-3">Priority</th>
                <th className="px-3 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {jobs.map((job) => {
                const totalFiles = estimatedTotalFiles(job);
                const progressValue = Math.min(100, (job.files_restored / totalFiles) * 100);
                const action = actionLabel(job.status);
                const isExpanded = expandedJobId === job.job_id;
                return (
                  <Fragment key={job.job_id}>
                    <tr className="cursor-pointer text-slate-200 hover:bg-quantum-sidebar/40" onClick={() => setExpandedJobId((current) => (current === job.job_id ? null : job.job_id))}>
                      <td className="px-3 py-3">
                        <div className="font-medium text-slate-100">{shortId(job.job_id)}</div>
                        <div className="mt-1 text-xs text-slate-500">{job.job_id}</div>
                      </td>
                      <td className="px-3 py-3">{job.pool_id ? poolNameMap.get(job.pool_id) ?? job.pool_id : '—'}</td>
                      <td className="px-3 py-3"><NasStatusBadge value={job.status} className={job.status.toUpperCase() === 'RUNNING' ? 'animate-pulse' : undefined} /></td>
                      <td className="px-3 py-3">{job.required_tapes.length}</td>
                      <td className="px-3 py-3">{job.missing_tapes.length}</td>
                      <td className="px-3 py-3 font-mono text-xs">{job.destination}</td>
                      <td className="px-3 py-3">
                        <div className="space-y-2">
                          <div className="text-xs text-slate-400">{job.files_restored}/{totalFiles}</div>
                          <div className="h-2 w-32 rounded-full bg-slate-800">
                            <div className="h-2 rounded-full bg-emerald-500" style={{ width: `${progressValue}%` }} />
                          </div>
                        </div>
                      </td>
                      <td className="px-3 py-3"><BytesDisplay value={job.bytes_restored} /></td>
                      <td className="px-3 py-3">{job.priority}</td>
                      <td className="px-3 py-3">
                        <div className="flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
                          {action ? (
                            <Button type="button" variant="secondary" disabled={jobActionMutation.isPending} onClick={() => jobActionMutation.mutate({ jobId: job.job_id, action: job.status.toUpperCase() === 'QUEUED' ? 'run' : job.status.toUpperCase() === 'RUNNING' ? 'pause' : job.status.toUpperCase() === 'PAUSED' ? 'resume' : 'retry' })}>
                              {action}
                            </Button>
                          ) : null}
                          {!['COMPLETED', 'CANCELLED'].includes(job.status.toUpperCase()) ? (
                            <Button type="button" variant="danger" disabled={jobActionMutation.isPending} onClick={() => jobActionMutation.mutate({ jobId: job.job_id, action: 'cancel' })}>
                              Cancel
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {isExpanded ? (
                      <tr className="bg-quantum-sidebar/40 text-slate-200">
                        <td className="px-3 py-4" colSpan={10}>
                          <div className="grid gap-4 lg:grid-cols-2">
                            <div className="space-y-3">
                              <div>
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Required tapes</div>
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {job.required_tapes.length > 0 ? job.required_tapes.map((tape) => (
                                    <span key={tape} className="rounded-full border border-quantum-border px-2.5 py-1 text-xs text-slate-200">{tape}</span>
                                  )) : <span className="text-sm text-slate-400">None</span>}
                                </div>
                              </div>
                              <div>
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Missing tapes</div>
                                <div className="mt-2 flex flex-wrap gap-2">
                                  {job.missing_tapes.length > 0 ? job.missing_tapes.map((tape) => (
                                    <span key={tape} className="rounded-full border border-red-500/30 bg-red-950/30 px-2.5 py-1 text-xs text-red-200">{tape}</span>
                                  )) : <span className="text-sm text-slate-400">None</span>}
                                </div>
                              </div>
                              <div>
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Errors</div>
                                <div className="mt-2 space-y-2">
                                  {job.errors.length > 0 ? job.errors.map((error) => (
                                    <div key={error} className="rounded-md border border-red-500/30 bg-red-950/20 px-3 py-2 text-sm text-red-200">{error}</div>
                                  )) : <div className="text-sm text-slate-400">No reported errors.</div>}
                                </div>
                              </div>
                            </div>
                            <div className="grid gap-3 md:grid-cols-2">
                              <div className="rounded-md border border-quantum-border px-3 py-3">
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Created</div>
                                <div className="mt-2 text-sm text-slate-100">{job.created_at ? formatDate(job.created_at) : '—'}</div>
                              </div>
                              <div className="rounded-md border border-quantum-border px-3 py-3">
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Updated</div>
                                <div className="mt-2 text-sm text-slate-100">{job.updated_at ? formatDate(job.updated_at) : '—'}</div>
                              </div>
                              <div className="rounded-md border border-quantum-border px-3 py-3">
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Completed</div>
                                <div className="mt-2 text-sm text-slate-100">{job.completed_at ? formatDate(job.completed_at) : '—'}</div>
                              </div>
                              <div className="rounded-md border border-quantum-border px-3 py-3">
                                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Bytes planned</div>
                                <div className="mt-2"><BytesDisplay value={job.estimated_bytes} /></div>
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
        {jobs.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-400">No restore jobs found.</div> : null}
      </Card>
    </div>
  );
}
