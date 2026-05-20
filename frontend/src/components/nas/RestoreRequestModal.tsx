import { useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  createRestorePlan,
  getDatasetFiles,
  requestRestore,
  type NasRestoreJob,
  type RestorePlan,
  type RestorePlanRequest,
} from '../../api/nas';
import BytesDisplay from './BytesDisplay';
import NasStatusBadge from './NasStatusBadge';
import Button from '../ui/Button';

interface RestoreRequestModalProps {
  poolId?: string | null;
  datasetId?: string | null;
  selectedPaths?: string[];
  onClose: () => void;
  onEnqueued: (jobId: string, job: NasRestoreJob) => void;
}

interface FormState {
  destination: string;
  priority: number;
  allow_parallel: boolean;
  max_drives: number;
}

const defaultForm: FormState = {
  destination: '/openblade/restore',
  priority: 5,
  allow_parallel: true,
  max_drives: 2,
};

function summarizePaths(paths: string[]): string {
  if (paths.length === 0) {
    return 'Entire pool';
  }
  if (paths.length === 1) {
    return paths[0];
  }
  return `${paths[0]} +${paths.length - 1} more`;
}

export default function RestoreRequestModal({
  poolId,
  datasetId,
  selectedPaths,
  onClose,
  onEnqueued,
}: RestoreRequestModalProps) {
  const [form, setForm] = useState<FormState>(defaultForm);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [plan, setPlan] = useState<RestorePlan | null>(null);

  const datasetFilesQuery = useQuery({
    queryKey: ['nas', 'dataset-files', datasetId, 'restore-modal'],
    queryFn: () => getDatasetFiles(datasetId!),
    enabled: Boolean(datasetId) && (!selectedPaths || selectedPaths.length === 0),
  });

  const requestedPaths = useMemo(
    () => (selectedPaths && selectedPaths.length > 0 ? selectedPaths : (datasetFilesQuery.data ?? []).map((file) => file.relative_path)),
    [datasetFilesQuery.data, selectedPaths],
  );

  const requestBody: RestorePlanRequest = {
    pool_id: poolId ?? undefined,
    paths: requestedPaths,
    destination: form.destination,
    priority: form.priority,
    allow_parallel: form.allow_parallel,
    max_drives: form.max_drives,
  };

  const planMutation = useMutation({
    mutationFn: () => {
      if (!poolId) {
        throw new Error('A pool is required to plan a restore.');
      }
      return createRestorePlan(requestBody);
    },
    onSuccess: (result) => {
      setPlan(result);
      setFeedback(null);
    },
    onError: (error) => {
      setPlan(null);
      setFeedback(error instanceof Error ? error.message : 'Unable to plan restore.');
    },
  });

  const enqueueMutation = useMutation({
    mutationFn: () => {
      if (!poolId) {
        throw new Error('A pool is required to enqueue a restore.');
      }
      return requestRestore(poolId, requestBody);
    },
    onSuccess: (job) => {
      setFeedback(null);
      onEnqueued(job.job_id, job);
    },
    onError: (error) => {
      setFeedback(error instanceof Error ? error.message : 'Unable to enqueue restore.');
    },
  });

  const isLoadingPaths = datasetFilesQuery.isLoading;
  const canSubmit = Boolean(poolId) && !isLoadingPaths && (Boolean(datasetId) || requestedPaths.length > 0 || !selectedPaths || selectedPaths.length === 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 py-8">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Restore queue</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">Request Restore</h2>
            <p className="mt-2 text-sm text-slate-400">{summarizePaths(requestedPaths)}</p>
          </div>
          <Button type="button" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <label className="block text-sm text-slate-300 md:col-span-2">
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
          <label className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200 md:col-span-2">
            <span>Allow parallel tape hydration</span>
            <input type="checkbox" checked={form.allow_parallel} onChange={(event) => setForm((current) => ({ ...current, allow_parallel: event.target.checked }))} />
          </label>
        </div>

        {isLoadingPaths ? <div className="mt-4 rounded-md border border-quantum-border px-4 py-3 text-sm text-slate-300">Loading dataset file list…</div> : null}
        {!isLoadingPaths && datasetId && requestedPaths.length === 0 ? (
          <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-900/20 px-4 py-3 text-sm text-amber-100">No dataset files were found for this restore request.</div>
        ) : null}
        {feedback ? <div className="mt-4 rounded-md border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-200">{feedback}</div> : null}

        {plan ? (
          <div className="mt-6 space-y-4 rounded-lg border border-quantum-border bg-quantum-sidebar p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Dry run</div>
                <div className="mt-2 text-sm text-slate-200">{plan.required_tapes.length} required tape(s)</div>
              </div>
              <NasStatusBadge value={plan.is_safe_to_enqueue ? 'completed' : 'failed'} label={plan.is_safe_to_enqueue ? 'Ready to enqueue' : 'Review warnings'} />
            </div>
            <div className="grid gap-3 md:grid-cols-4">
              <div className="rounded-md border border-quantum-border px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Files</div>
                <div className="mt-2 text-sm text-slate-100">{plan.requested_paths.length || requestedPaths.length}</div>
              </div>
              <div className="rounded-md border border-quantum-border px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Bytes</div>
                <div className="mt-2"><BytesDisplay value={plan.estimated_bytes} /></div>
              </div>
              <div className="rounded-md border border-quantum-border px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Missing tapes</div>
                <div className="mt-2 text-sm text-slate-100">{plan.missing_tapes.length}</div>
              </div>
              <div className="rounded-md border border-quantum-border px-3 py-3">
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape swaps</div>
                <div className="mt-2 text-sm text-slate-100">{plan.estimated_tape_swaps}</div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              {plan.required_tapes.map((tape) => (
                <span key={tape} className="rounded-full border border-quantum-border px-2.5 py-1 text-xs text-slate-200">{tape}</span>
              ))}
            </div>
            {plan.warnings.length > 0 ? (
              <div className="space-y-2">
                {plan.warnings.map((warning) => (
                  <div key={warning} className="rounded-md border border-amber-500/30 bg-amber-900/20 px-3 py-2 text-sm text-amber-100">{warning}</div>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" variant="secondary" disabled={!canSubmit || planMutation.isPending || enqueueMutation.isPending} onClick={() => {
            setFeedback(null);
            setPlan(null);
            planMutation.mutate();
          }}>
            {planMutation.isPending ? 'Planning…' : 'Plan Restore'}
          </Button>
          <Button type="button" disabled={!canSubmit || enqueueMutation.isPending} onClick={() => {
            setFeedback(null);
            enqueueMutation.mutate();
          }}>
            {enqueueMutation.isPending ? 'Enqueueing…' : 'Enqueue Restore'}
          </Button>
        </div>
      </div>
    </div>
  );
}
