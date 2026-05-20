import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getSourceStream, updateSourceStream, type SourceStreamChecksumMode, type SourceStreamConfig, type SourceStreamRetryPolicy } from '../../api/nas';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { toTitleCase } from '../../lib/utils';

const checksumModes: SourceStreamChecksumMode[] = ['precompute', 'streaming', 'post_verify', 'precompute_and_post_verify'];
const retryPolicies: SourceStreamRetryPolicy[] = ['none', 'linear', 'exponential'];

function ToggleField({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start justify-between gap-4 rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200">
      <span>
        <span className="block font-medium text-slate-100">{label}</span>
        {description ? <span className="mt-1 block text-xs text-slate-500">{description}</span> : null}
      </span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-1" />
    </label>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

export default function SourceStreaming() {
  const queryClient = useQueryClient();
  const sourceStreamQuery = useQuery({ queryKey: ['nas', 'source-stream'], queryFn: getSourceStream });
  const [form, setForm] = useState<SourceStreamConfig | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  useEffect(() => {
    if (sourceStreamQuery.data) {
      setForm(sourceStreamQuery.data);
    }
  }, [sourceStreamQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: SourceStreamConfig) => updateSourceStream(payload),
    onSuccess: async (saved) => {
      setForm(saved);
      setFeedback({ type: 'success', message: 'Source-stream settings saved.' });
      await queryClient.invalidateQueries({ queryKey: ['nas', 'source-stream'] });
    },
    onError: (error) => {
      setFeedback({ type: 'error', message: error instanceof Error ? error.message : 'Unable to save source-stream settings.' });
    },
  });

  if (sourceStreamQuery.isLoading) {
    return <Spinner />;
  }

  if (sourceStreamQuery.isError) {
    return <ErrorMessage error={sourceStreamQuery.error} onRetry={() => void sourceStreamQuery.refetch()} />;
  }

  if (!form) {
    return <Spinner />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Source Streaming</h1>
          <p className="mt-2 text-sm text-slate-400">Manage the global source-stream ingest safety policy used when datasets are archived directly from NAS shares.</p>
        </div>
      </Card>

      <div className="rounded-md border border-amber-500/30 bg-amber-900/20 px-4 py-3 text-sm text-amber-100">
        Source-stream mode requires source files to remain stable and online. For critical datasets, cache-drive mode is safer unless the source supports snapshots.
      </div>

      <Card>
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-4">
            <ToggleField label="Enabled" checked={form.enabled} onChange={(checked) => setForm((current) => current ? { ...current, enabled: checked } : current)} description="Allow operators to choose source-stream ingest mode." />
            <ToggleField label="Require source online for entire job" checked={form.require_source_online_for_entire_job} onChange={(checked) => setForm((current) => current ? { ...current, require_source_online_for_entire_job: checked } : current)} description="Fail the job if the source becomes unavailable mid-run." />
            <ToggleField label="Preflight read check" checked={form.preflight_read_check} onChange={(checked) => setForm((current) => current ? { ...current, preflight_read_check: checked } : current)} description="Probe the source before any tape work begins." />
            <ToggleField label="Fail on source change" checked={form.fail_on_source_change} onChange={(checked) => setForm((current) => current ? { ...current, fail_on_source_change: checked } : current)} description="Abort if source metadata changes during ingest." />
            <ToggleField label="Snapshot required" checked={form.snapshot_required} onChange={(checked) => setForm((current) => current ? { ...current, snapshot_required: checked } : current)} description="Require snapshot-capable sources for source-stream workflows." />
            <ToggleField label="Allow partial dataset success" checked={form.allow_partial_dataset_success} onChange={(checked) => setForm((current) => current ? { ...current, allow_partial_dataset_success: checked } : current)} description="Permit partial completion when only a subset of files succeeds." />
          </div>

          <div className="space-y-4">
            <Field label="Checksum mode">
              <select value={form.checksum_mode} onChange={(event) => setForm((current) => current ? { ...current, checksum_mode: event.target.value as SourceStreamChecksumMode } : current)}>
                {checksumModes.map((mode) => (
                  <option key={mode} value={mode}>{toTitleCase(mode)}</option>
                ))}
              </select>
            </Field>
            <Field label="Retry policy">
              <select value={form.retry_policy} onChange={(event) => setForm((current) => current ? { ...current, retry_policy: event.target.value as SourceStreamRetryPolicy } : current)}>
                {retryPolicies.map((mode) => (
                  <option key={mode} value={mode}>{toTitleCase(mode)}</option>
                ))}
              </select>
            </Field>
            <Field label="Max retries">
              <input type="number" min={0} value={form.max_retries} onChange={(event) => setForm((current) => current ? { ...current, max_retries: Number(event.target.value) || 0 } : current)} />
            </Field>
            <Field label="Source change detection">
              <input value={form.source_change_detection} onChange={(event) => setForm((current) => current ? { ...current, source_change_detection: event.target.value } : current)} />
            </Field>
          </div>
        </div>

        {feedback ? (
          <div className={`mt-4 rounded-md border px-4 py-3 text-sm ${feedback.type === 'success' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-red-500/30 bg-red-950/20 text-red-200'}`}>
            {feedback.message}
          </div>
        ) : null}

        <div className="mt-6 flex justify-end">
          <Button type="button" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate(form)}>
            {saveMutation.isPending ? 'Saving…' : 'Save Settings'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
