import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { createArchivePlan, listPolicies, type ArchivePlan, type ArchivePlanWarning, type IngestMode } from '../../api/nas';
import BytesDisplay from '../../components/nas/BytesDisplay';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { toTitleCase } from '../../lib/utils';

interface PlanFormState {
  source_path: string;
  policy_id: string;
  ingest_mode: IngestMode;
  tape_barcodes: string;
  file_listing: string;
  estimated_bytes: number;
}

const emptyForm: PlanFormState = {
  source_path: '',
  policy_id: '',
  ingest_mode: 'cache_drive',
  tape_barcodes: '',
  file_listing: '',
  estimated_bytes: 0,
};

function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

function distributeBytes(files: string[], totalBytes: number): Record<string, number> {
  if (files.length === 0 || totalBytes <= 0) {
    return Object.fromEntries(files.map((file) => [file, 0]));
  }

  const baseSize = Math.floor(totalBytes / files.length);
  let remainder = totalBytes - baseSize * files.length;

  return Object.fromEntries(
    files.map((file) => {
      const extra = remainder > 0 ? 1 : 0;
      remainder = Math.max(remainder - 1, 0);
      return [file, baseSize + extra];
    }),
  );
}

function WarningBanner({ warning, tone = 'warning' }: { warning: ArchivePlanWarning | { message: string; field: string | null; level: string }; tone?: 'warning' | 'danger' }) {
  return (
    <div className={`rounded-md border px-4 py-3 text-sm ${tone === 'danger' ? 'border-red-500/30 bg-red-950/20 text-red-200' : 'border-amber-500/30 bg-amber-900/20 text-amber-100'}`}>
      {warning.message}
      {warning.field ? <span className="ml-2 text-xs uppercase tracking-[0.16em] opacity-80">{warning.field}</span> : null}
    </div>
  );
}

export default function ArchivePlanning() {
  const policiesQuery = useQuery({ queryKey: ['nas', 'policies'], queryFn: listPolicies });
  const [form, setForm] = useState<PlanFormState>(emptyForm);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [plan, setPlan] = useState<ArchivePlan | null>(null);

  useEffect(() => {
    if (!form.policy_id && policiesQuery.data?.[0]) {
      setForm((current) => ({ ...current, policy_id: policiesQuery.data?.[0]?.id ?? '' }));
    }
  }, [form.policy_id, policiesQuery.data]);

  const planMutation = useMutation({
    mutationFn: () => {
      const files = parseLines(form.file_listing);
      const tapes = parseLines(form.tape_barcodes);
      return createArchivePlan({
        source_path: form.source_path || undefined,
        policy_id: form.policy_id || undefined,
        ingest_mode: form.ingest_mode,
        files,
        file_sizes: distributeBytes(files, form.estimated_bytes),
        available_tapes: tapes,
      });
    },
    onSuccess: (result) => {
      setPlan(result);
      setFeedback(null);
    },
    onError: (error) => {
      setPlan(null);
      setFeedback(error instanceof Error ? error.message : 'Unable to create archive plan.');
    },
  });

  const warnings = useMemo(() => plan ? [...plan.capacity_warnings, ...plan.safety_warnings] : [], [plan]);

  if (policiesQuery.isLoading) {
    return <Spinner />;
  }

  if (policiesQuery.isError) {
    return <ErrorMessage error={policiesQuery.error} onRetry={() => void policiesQuery.refetch()} />;
  }

  const policies = policiesQuery.data ?? [];

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Archive Planning</h1>
          <p className="mt-2 text-sm text-slate-400">Run a dry-run planner against simulated tape availability and file lists before you enqueue a NAS archive job.</p>
        </div>
      </Card>

      <Card>
        <form
          className="grid gap-4 xl:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            const files = parseLines(form.file_listing);
            const tapes = parseLines(form.tape_barcodes);
            if (!form.source_path.trim()) {
              setFeedback('Source path is required.');
              return;
            }
            if (files.length === 0) {
              setFeedback('Add at least one file path to the simulated file listing.');
              return;
            }
            if (tapes.length === 0) {
              setFeedback('Add at least one tape barcode.');
              return;
            }
            setFeedback(null);
            setPlan(null);
            planMutation.mutate();
          }}
        >
          <label className="block text-sm text-slate-300 xl:col-span-2">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Source path</span>
            <input value={form.source_path} onChange={(event) => setForm((current) => ({ ...current, source_path: event.target.value }))} placeholder="/nas/projects/project-a" required />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Policy</span>
            <select value={form.policy_id} onChange={(event) => setForm((current) => ({ ...current, policy_id: event.target.value }))}>
              {policies.map((policy) => (
                <option key={policy.id} value={policy.id}>{policy.name}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Ingest mode</span>
            <select value={form.ingest_mode} onChange={(event) => setForm((current) => ({ ...current, ingest_mode: event.target.value as IngestMode }))}>
              <option value="cache_drive">Cache Drive</option>
              <option value="source_stream">Source Stream</option>
            </select>
          </label>
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Tape barcodes</span>
            <textarea className="min-h-40" value={form.tape_barcodes} onChange={(event) => setForm((current) => ({ ...current, tape_barcodes: event.target.value }))} placeholder={'TAPE001\nTAPE002'} />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">File listing</span>
            <textarea className="min-h-40" value={form.file_listing} onChange={(event) => setForm((current) => ({ ...current, file_listing: event.target.value }))} placeholder={'/nas/projects/project-a/file-001.bin\n/nas/projects/project-a/file-002.bin'} />
          </label>
          <label className="block text-sm text-slate-300 xl:col-span-2">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Estimated bytes</span>
            <input type="number" min={0} value={form.estimated_bytes} onChange={(event) => setForm((current) => ({ ...current, estimated_bytes: Number(event.target.value) || 0 }))} />
          </label>

          {feedback ? (
            <div className="rounded-md border border-red-500/30 bg-red-950/20 px-4 py-3 text-sm text-red-200 xl:col-span-2">{feedback}</div>
          ) : null}

          <div className="flex justify-end xl:col-span-2">
            <Button type="submit" disabled={planMutation.isPending || policies.length === 0}>{planMutation.isPending ? 'Planning…' : 'Create Plan'}</Button>
          </div>
        </form>
      </Card>

      {plan ? (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Plan result</div>
              <h2 className="mt-1 text-xl font-semibold text-slate-100">{plan.policy_name ?? 'Archive plan'}</h2>
            </div>
            <NasStatusBadge value={plan.is_safe_to_enqueue ? 'safe' : 'not safe'} label={plan.is_safe_to_enqueue ? 'Safe to enqueue' : 'Not safe — see warnings'} />
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Policy</div>
              <div className="mt-2 text-sm text-slate-100">{plan.policy_name ?? '—'}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Policy type</div>
              <div className="mt-2"><NasStatusBadge value={plan.policy_type ?? 'balanced'} label={plan.policy_type ? toTitleCase(plan.policy_type) : 'Balanced'} /></div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Ingest mode</div>
              <div className="mt-2"><NasStatusBadge value={plan.ingest_mode} /></div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Estimated swaps</div>
              <div className="mt-2 text-sm text-slate-100">{plan.estimated_tape_swaps}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Total bytes</div>
              <div className="mt-2"><BytesDisplay value={plan.total_bytes} /></div>
            </div>
          </div>

          <div className="mt-6 overflow-x-auto">
            <table className="min-w-full divide-y divide-quantum-border text-sm">
              <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                <tr>
                  <th className="px-3 py-3">Tape barcode</th>
                  <th className="px-3 py-3">File count</th>
                  <th className="px-3 py-3">Bytes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-quantum-border/80">
                {plan.tape_assignments.map((assignment) => (
                  <tr key={assignment.barcode} className="text-slate-200">
                    <td className="px-3 py-3 font-mono text-xs text-slate-100">{assignment.barcode}</td>
                    <td className="px-3 py-3">{assignment.files.length}</td>
                    <td className="px-3 py-3"><BytesDisplay value={assignment.estimated_bytes} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-6 space-y-3">
            {warnings.map((warning) => (
              <WarningBanner key={`${warning.field ?? 'warning'}-${warning.message}`} warning={warning} />
            ))}
            {plan.enqueue_blockers.map((blocker) => (
              <WarningBanner key={blocker} warning={{ level: 'error', message: blocker, field: null }} tone="danger" />
            ))}
          </div>
        </Card>
      ) : null}
    </div>
  );
}
