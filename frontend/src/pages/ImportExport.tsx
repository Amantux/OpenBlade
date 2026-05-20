import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  closeIeDoor,
  getExportStatus,
  getImportStatus,
  listIeStations,
  openIeDoor,
  startExport,
  startImport,
} from '../api/operations';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

function stateClass(state: string): string {
  switch (state.toLowerCase()) {
    case 'running':
      return 'border-blue-500/30 bg-blue-500/15 text-blue-300';
    case 'completed':
      return 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300';
    default:
      return 'border-slate-700 bg-slate-800 text-slate-200';
  }
}

export default function ImportExport() {
  const queryClient = useQueryClient();
  const importQuery = useQuery({ queryKey: ['operations', 'import', 'status'], queryFn: getImportStatus, refetchInterval: 5_000 });
  const exportQuery = useQuery({ queryKey: ['operations', 'export', 'status'], queryFn: getExportStatus, refetchInterval: 5_000 });
  const stationsQuery = useQuery({ queryKey: ['operations', 'ie-stations'], queryFn: listIeStations, refetchInterval: 10_000 });

  const refreshAll = () => Promise.all([
    queryClient.invalidateQueries({ queryKey: ['operations', 'import', 'status'] }),
    queryClient.invalidateQueries({ queryKey: ['operations', 'export', 'status'] }),
    queryClient.invalidateQueries({ queryKey: ['operations', 'ie-stations'] }),
  ]);

  const primaryStation = stationsQuery.data?.[0];
  const openDoorMutation = useMutation({ mutationFn: () => openIeDoor(primaryStation?.id), onSuccess: async () => { await refreshAll(); } });
  const closeDoorMutation = useMutation({ mutationFn: () => closeIeDoor(primaryStation?.id), onSuccess: async () => { await refreshAll(); } });
  const importMutation = useMutation({ mutationFn: startImport, onSuccess: async () => { await refreshAll(); } });
  const exportMutation = useMutation({ mutationFn: startExport, onSuccess: async () => { await refreshAll(); } });

  if (importQuery.isLoading || exportQuery.isLoading || stationsQuery.isLoading) {
    return <Spinner />;
  }
  if (importQuery.isError) {
    return <ErrorMessage error={importQuery.error} onRetry={() => importQuery.refetch()} />;
  }
  if (exportQuery.isError) {
    return <ErrorMessage error={exportQuery.error} onRetry={() => exportQuery.refetch()} />;
  }
  if (stationsQuery.isError) {
    return <ErrorMessage error={stationsQuery.error} onRetry={() => stationsQuery.refetch()} />;
  }

  const stations = stationsQuery.data ?? [];
  const slotCount = stations.reduce((sum, station) => sum + station.slotCount, 0);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Operations Center</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">Import / Export</h1>
            <p className="mt-2 text-sm text-slate-400">Control the primary IE station, check current transfer state, and start basic import/export workflows.</p>
          </div>
          <Button variant="secondary" onClick={() => void Promise.all([importQuery.refetch(), exportQuery.refetch(), stationsQuery.refetch()])}>
            Refresh
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Import</p>
              <h2 className="mt-2 text-xl font-semibold text-white">Media intake</h2>
            </div>
            <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateClass(importQuery.data.state)}`}>
              {importQuery.data.state.toUpperCase()}
            </span>
          </div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div>IE Station: <span className="font-semibold text-white">{primaryStation?.id ?? 'Unavailable'}</span></div>
            <div>Door state: <span className="font-semibold text-white">{primaryStation?.state ?? 'Unknown'}</span></div>
            <div>Slot count: <span className="font-semibold text-white">{slotCount}</span></div>
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button variant="secondary" disabled={!primaryStation || openDoorMutation.isPending} onClick={() => openDoorMutation.mutate()}>
              {openDoorMutation.isPending ? 'Opening…' : 'Open IE Door'}
            </Button>
            <Button disabled={!primaryStation || importMutation.isPending} onClick={() => importMutation.mutate()}>
              {importMutation.isPending ? 'Starting…' : 'Start Import'}
            </Button>
          </div>
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-slate-500">Export</p>
              <h2 className="mt-2 text-xl font-semibold text-white">Media handoff</h2>
            </div>
            <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateClass(exportQuery.data.state)}`}>
              {exportQuery.data.state.toUpperCase()}
            </span>
          </div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div>IE Station: <span className="font-semibold text-white">{primaryStation?.id ?? 'Unavailable'}</span></div>
            <div>Door state: <span className="font-semibold text-white">{primaryStation?.state ?? 'Unknown'}</span></div>
            <div>Slot count: <span className="font-semibold text-white">{slotCount}</span></div>
          </div>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button disabled={!primaryStation || exportMutation.isPending} onClick={() => exportMutation.mutate()}>
              {exportMutation.isPending ? 'Starting…' : 'Start Export'}
            </Button>
            <Button variant="secondary" disabled={!primaryStation || closeDoorMutation.isPending} onClick={() => closeDoorMutation.mutate()}>
              {closeDoorMutation.isPending ? 'Closing…' : 'Close IE Door'}
            </Button>
          </div>
        </Card>
      </div>

      {openDoorMutation.isError ? <ErrorMessage error={openDoorMutation.error} /> : null}
      {closeDoorMutation.isError ? <ErrorMessage error={closeDoorMutation.error} /> : null}
      {importMutation.isError ? <ErrorMessage error={importMutation.error} /> : null}
      {exportMutation.isError ? <ErrorMessage error={exportMutation.error} /> : null}
    </div>
  );
}
