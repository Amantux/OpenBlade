import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { apiRequest } from '../api/client';
import { useInventory } from '../hooks/useInventory';
import { useJobs } from '../hooks/useJobs';
import { getJobBarcode, getJobState, getJobStrategy, getJobTypeLabel, normalizeSlot } from '../lib/lmc';
import { formatDate } from '../lib/utils';
import type { JobResponse } from '../types/api';

interface ArchiveSubmission {
  source_path: string;
  barcode: string;
  strategy: 'single' | 'STRIPE' | 'BLOCK_STRIPE';
}

const strategyOptions = [
  { label: 'Single Drive', value: 'single' as const },
  { label: 'Parallel Stripe', value: 'STRIPE' as const },
  { label: 'Block Stripe', value: 'BLOCK_STRIPE' as const },
];

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

export default function Archive() {
  const queryClient = useQueryClient();
  const inventoryQuery = useInventory();
  const jobsQuery = useJobs();
  const [sourcePath, setSourcePath] = useState('');
  const [barcode, setBarcode] = useState('');
  const [strategy, setStrategy] = useState<ArchiveSubmission['strategy']>('single');
  const [selectedJobId, setSelectedJobId] = useState<string>();

  const mutation = useMutation({
    mutationFn: (payload: ArchiveSubmission) =>
      apiRequest<{ job_id?: string; id?: string; status: string }>('/archive/', {
        method: 'POST',
        body: payload,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['jobs'] });
      setSourcePath('');
    },
  });

  const inventory = inventoryQuery.data ?? { library_id: 'LIBRARY-01', slots: [], drives: [], changer_state: 'UNKNOWN' };
  const jobs = jobsQuery.data ?? [];
  const recentArchives = useMemo(
    () => jobs.filter((job) => getJobTypeLabel(job).toLowerCase().includes('archive')),
    [jobs],
  );
  const barcodes = inventory.slots.map(normalizeSlot).flatMap((slot) => (slot.barcode ? [slot.barcode] : []));

  useEffect(() => {
    if (!barcode && barcodes.length > 0) {
      setBarcode(barcodes[0]);
    }
  }, [barcode, barcodes]);

  useEffect(() => {
    if (!selectedJobId && recentArchives.length > 0) {
      setSelectedJobId(recentArchives[0].id);
    }
  }, [recentArchives, selectedJobId]);

  const selectedJob = recentArchives.find((job) => job.id === selectedJobId) ?? recentArchives[0];

  if (inventoryQuery.isLoading || jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="border-b border-quantum-border pb-3">
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Operations Panel</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Archive Operation</h2>
          <p className="mt-1 text-sm text-slate-400">Submit single-drive or striped archive operations to the selected barcode.</p>
        </div>
        <form
          className="mt-4 grid gap-4 xl:grid-cols-[1.4fr,0.8fr,0.8fr,auto] xl:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate({ source_path: sourcePath, barcode, strategy });
          }}
        >
          <div>
            <label className="text-sm font-medium text-slate-300">Source Path</label>
            <input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="/data/project-a" required />
          </div>
          <div>
            <label className="text-sm font-medium text-slate-300">Target Barcode</label>
            <input list="archive-barcodes" value={barcode} onChange={(event) => setBarcode(event.target.value)} placeholder="ARC001L8" required />
            <datalist id="archive-barcodes">
              {barcodes.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </div>
          <div>
            <label className="text-sm font-medium text-slate-300">Strategy</label>
            <select value={strategy} onChange={(event) => setStrategy(event.target.value as ArchiveSubmission['strategy'])}>
              {strategyOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <Button type="submit" disabled={mutation.isPending || !sourcePath || !barcode} variant="primary">
            {mutation.isPending ? 'Submitting…' : 'Submit'}
          </Button>
        </form>
        {mutation.isError ? <div className="mt-4"><ErrorMessage error={mutation.error} /></div> : null}
        {mutation.isSuccess ? (
          <div className="mt-4 rounded-md border border-emerald-700 bg-emerald-900/20 px-3 py-3 text-sm text-emerald-200">
            Archive request queued successfully.
          </div>
        ) : null}
      </Card>

      <NorthPanel
        title="Recent Archives"
        subtitle="Most recent archive jobs submitted through the LMC."
        columns={[
          { key: 'id', header: 'Job ID', render: (row: JobResponse) => <span className="font-mono text-xs">{row.id}</span> },
          { key: 'state', header: 'State', render: (row: JobResponse) => <Badge variant={stateVariant(getJobState(row))}>{getJobState(row)}</Badge> },
          { key: 'barcode', header: 'Barcode', render: (row: JobResponse) => getJobBarcode(row) },
          { key: 'strategy', header: 'Strategy', render: (row: JobResponse) => getJobStrategy(row) },
          { key: 'started', header: 'Started', render: (row: JobResponse) => formatDate(row.created_at) },
        ]}
        rows={recentArchives}
        getRowId={(row) => row.id}
        selectedId={selectedJob?.id}
        onSelect={(row) => setSelectedJobId(row.id)}
        emptyMessage="No archive requests have been submitted yet."
      />

      <InformationPanel
        title={selectedJob ? `Archive Job ${selectedJob.id}` : 'Archive Guidance'}
        subtitle="Current selection and operator reference for archive workflows."
        items={[
          { label: 'Selected Barcode', value: selectedJob ? getJobBarcode(selectedJob) : barcode || '—' },
          { label: 'Strategy', value: selectedJob ? getJobStrategy(selectedJob) : strategyOptions.find((option) => option.value === strategy)?.label ?? '—' },
          { label: 'State', value: selectedJob ? getJobState(selectedJob) : 'Ready' },
          { label: 'Source Path', value: selectedJob?.metadata?.source_path ? String(selectedJob.metadata.source_path) : sourcePath || '—' },
        ]}
      />
    </div>
  );
}
