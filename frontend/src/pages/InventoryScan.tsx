import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getInventoryResult, getInventoryStatus, runInventory } from '../api/operations';
import type { InventoryResult } from '../types/api';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function stateTone(state: string): string {
  switch (state.toLowerCase()) {
    case 'running':
      return 'border-blue-500/30 bg-blue-500/15 text-blue-300';
    case 'completed':
      return 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300';
    default:
      return 'border-slate-700 bg-slate-800 text-slate-200';
  }
}

export default function InventoryScan() {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ['operations', 'inventory', 'status'],
    queryFn: getInventoryStatus,
    refetchInterval: (query) => (query.state.data?.state.toLowerCase() === 'running' ? 2_000 : false),
  });
  const resultQuery = useQuery({
    queryKey: ['operations', 'inventory', 'result'],
    queryFn: getInventoryResult,
  });

  const runMutation = useMutation({
    mutationFn: runInventory,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['operations', 'inventory', 'status'] }),
        queryClient.invalidateQueries({ queryKey: ['operations', 'inventory', 'result'] }),
      ]);
    },
  });

  if (statusQuery.isLoading || resultQuery.isLoading) {
    return <Spinner />;
  }
  if (statusQuery.isError) {
    return <ErrorMessage error={statusQuery.error} onRetry={() => statusQuery.refetch()} />;
  }
  if (resultQuery.isError) {
    return <ErrorMessage error={resultQuery.error} onRetry={() => resultQuery.refetch()} />;
  }

  const status = statusQuery.data ?? {
    state: 'idle',
    startTime: null,
    completedTime: null,
    progress: 0,
    elementsScanned: 0,
    elementsTotal: 0,
    lastCompleted: null,
  };
  const result: InventoryResult = resultQuery.data ?? {
    timestamp: null,
    elementsScanned: 0,
    mediaFound: 0,
    emptySlots: 0,
    errors: [],
  };
  const progress = status.elementsTotal > 0 ? Math.max(status.progress, Math.round((status.elementsScanned / status.elementsTotal) * 100)) : status.progress;

  return (
    <div className="space-y-4">
      <Card>
        <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Operations Center</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Inventory scan</h1>
        <p className="mt-2 text-sm text-slate-400">Run a full AML inventory and watch status updates every two seconds while the scan is active.</p>
      </Card>

      <Card>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div className="space-y-4">
            <div className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateTone(status.state)}`}>
              {status.state.toUpperCase()}
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Progress</div>
                <div className="mt-2 text-2xl font-semibold text-white">{progress}%</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Elements scanned</div>
                <div className="mt-2 text-2xl font-semibold text-white">{status.elementsScanned}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Elements total</div>
                <div className="mt-2 text-2xl font-semibold text-white">{status.elementsTotal}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Last completed</div>
                <div className="mt-2 text-sm font-semibold text-white">{formatDate(status.lastCompleted ?? '')}</div>
              </div>
            </div>
          </div>

          <div className="flex gap-3">
            <Button variant="secondary" onClick={() => void Promise.all([statusQuery.refetch(), resultQuery.refetch()])}>Refresh</Button>
            <Button disabled={runMutation.isPending || status.state.toLowerCase() === 'running'} onClick={() => runMutation.mutate()}>
              {runMutation.isPending ? 'Starting…' : 'Run Full Inventory'}
            </Button>
          </div>
        </div>

        <div className="mt-6">
          <div className="h-3 overflow-hidden rounded-full bg-slate-800">
            <div className="h-full bg-quantum-red transition-all" style={{ width: `${progress}%` }} />
          </div>
        </div>
      </Card>

      <Card>
        <h2 className="text-lg font-semibold text-white">Result summary</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Media found</div>
            <div className="mt-2 text-2xl font-semibold text-white">{result.mediaFound}</div>
          </div>
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Empty slots</div>
            <div className="mt-2 text-2xl font-semibold text-white">{result.emptySlots}</div>
          </div>
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Completed at</div>
            <div className="mt-2 text-sm font-semibold text-white">{formatDate(result.timestamp ?? '')}</div>
          </div>
        </div>
        <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-300">
          {result.errors.length > 0 ? result.errors.join(', ') : 'No inventory errors reported.'}
        </div>
      </Card>

      {runMutation.isError ? <ErrorMessage error={runMutation.error} /> : null}
    </div>
  );
}
