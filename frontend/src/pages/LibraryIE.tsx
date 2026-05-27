import { useMemo, useState } from 'react';
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
import { useLibraryScope } from '../lib/useLibraryScope';

function stateClass(state?: string): string {
  switch ((state ?? '').toLowerCase()) {
    case 'running':
    case 'open':
    case 'completed':
      return 'border-quantum-red/30 bg-quantum-red/10 text-red-200';
    case 'closed':
    case 'warning':
      return 'border-red-700/40 bg-red-950/40 text-red-100';
    default:
      return 'border-quantum-border bg-quantum-panel text-slate-200';
  }
}

export default function LibraryIE() {
  const queryClient = useQueryClient();
  const { libraryName } = useLibraryScope();
  const [selectedStationId, setSelectedStationId] = useState<string>();

  const importQuery = useQuery({
    queryKey: ['operations', 'import', 'status'],
    queryFn: getImportStatus,
    refetchInterval: 5_000,
  });
  const exportQuery = useQuery({
    queryKey: ['operations', 'export', 'status'],
    queryFn: getExportStatus,
    refetchInterval: 5_000,
  });
  const stationsQuery = useQuery({
    queryKey: ['operations', 'ie-stations'],
    queryFn: listIeStations,
    refetchInterval: 10_000,
  });

  const refreshAll = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ['operations', 'import', 'status'] }),
      queryClient.invalidateQueries({ queryKey: ['operations', 'export', 'status'] }),
      queryClient.invalidateQueries({ queryKey: ['operations', 'ie-stations'] }),
    ]);

  const selectedStation = useMemo(
    () => stationsQuery.data?.find((station) => station.id === selectedStationId) ?? stationsQuery.data?.[0],
    [selectedStationId, stationsQuery.data],
  );
  const selectedBarcode = useMemo(
    () => selectedStation?.slots.find((slot) => slot.barcode)?.barcode ?? undefined,
    [selectedStation],
  );

  const openDoorMutation = useMutation({
    mutationFn: () => openIeDoor(selectedStation?.id),
    onSuccess: async () => {
      await refreshAll();
    },
  });
  const closeDoorMutation = useMutation({
    mutationFn: () => closeIeDoor(selectedStation?.id),
    onSuccess: async () => {
      await refreshAll();
    },
  });
  const importMutation = useMutation({
    mutationFn: () => startImport(selectedStation?.id),
    onSuccess: async () => {
      await refreshAll();
    },
  });
  const exportMutation = useMutation({
    mutationFn: () => startExport(selectedStation?.id, selectedBarcode),
    onSuccess: async () => {
      await refreshAll();
    },
  });

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
  const importStatus = importQuery.data ?? { state: 'idle' };
  const exportStatus = exportQuery.data ?? { state: 'idle' };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.22em] text-red-300/70">Library</p>
            <h1 className="text-2xl font-semibold text-white">IE Station</h1>
            <p className="max-w-3xl text-sm text-slate-400">
              Monitor import/export stations, inspect mail-slot occupancy, and run guarded door or
              media-transfer actions for {libraryName || 'the active library'}.
            </p>
          </div>
          <Button
            variant="secondary"
            onClick={() => void Promise.all([importQuery.refetch(), exportQuery.refetch(), stationsQuery.refetch()])}
          >
            Refresh
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Stations</p>
              <h2 className="mt-2 text-xl font-semibold text-white">Available IE stations</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateClass(importStatus.state)}`}>
                Import {importStatus.state.toUpperCase()}
              </span>
              <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateClass(exportStatus.state)}`}>
                Export {exportStatus.state.toUpperCase()}
              </span>
            </div>
          </div>

          <div className="mt-4 grid gap-3">
            {stations.map((station) => {
              const occupiedSlots = station.slots.filter((slot) => slot.barcode).length;
              const isSelected = station.id === selectedStation?.id;
              return (
                <button
                  key={station.id}
                  type="button"
                  onClick={() => setSelectedStationId(station.id)}
                  className={`rounded-lg border p-4 text-left transition ${
                    isSelected
                      ? 'border-quantum-red bg-quantum-north/70'
                      : 'border-quantum-border bg-quantum-panel hover:bg-quantum-north/40'
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="text-sm font-semibold text-white">{station.id}</div>
                      <div className="mt-1 text-xs text-slate-400">{station.serialNumber}</div>
                    </div>
                    <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${stateClass(station.state)}`}>
                      {station.state.toUpperCase()}
                    </span>
                  </div>
                  <div className="mt-4 grid gap-2 text-sm text-slate-300 sm:grid-cols-3">
                    <div>
                      Slots <span className="font-semibold text-white">{station.slotCount}</span>
                    </div>
                    <div>
                      Occupied <span className="font-semibold text-white">{occupiedSlots}</span>
                    </div>
                    <div>
                      Status <span className="font-semibold text-white">{station.status}</span>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        </Card>

        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Selected station</p>
              <h2 className="mt-2 text-xl font-semibold text-white">{selectedStation?.id ?? 'No station selected'}</h2>
            </div>
            <span className={`inline-flex rounded-full border px-3 py-1 text-sm font-semibold ${stateClass(selectedStation?.state ?? 'unknown')}`}>
              {(selectedStation?.state ?? 'unknown').toUpperCase()}
            </span>
          </div>

          <div className="mt-4 space-y-2 text-sm text-slate-300">
            <div>
              Serial number <span className="font-semibold text-red-100">{selectedStation?.serialNumber ?? '—'}</span>
            </div>
            <div>
              Slots <span className="font-semibold text-red-100">{selectedStation?.slotCount ?? 0}</span>
            </div>
            <div>
              Occupied barcode <span className="font-semibold text-red-100">{selectedBarcode ?? 'None loaded'}</span>
            </div>
          </div>

          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              variant="secondary"
              disabled={!selectedStation || openDoorMutation.isPending}
              onClick={() => openDoorMutation.mutate()}
            >
              {openDoorMutation.isPending ? 'Opening…' : 'Open IE Door'}
            </Button>
            <Button
              variant="secondary"
              disabled={!selectedStation || closeDoorMutation.isPending}
              onClick={() => closeDoorMutation.mutate()}
            >
              {closeDoorMutation.isPending ? 'Closing…' : 'Close IE Door'}
            </Button>
            <Button
              disabled={!selectedStation || importMutation.isPending}
              onClick={() => importMutation.mutate()}
            >
              {importMutation.isPending ? 'Starting…' : 'Start Import'}
            </Button>
            <Button
              disabled={!selectedStation || !selectedBarcode || exportMutation.isPending}
              onClick={() => exportMutation.mutate()}
            >
              {exportMutation.isPending ? 'Starting…' : 'Export Loaded Media'}
            </Button>
          </div>
        </Card>
      </div>

      <Card>
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Slot occupancy</p>
            <h2 className="mt-2 text-xl font-semibold text-white">IE station slots</h2>
          </div>
          <span className="text-sm text-slate-400">
            {selectedStation?.slots.length ?? 0} visible slots
          </span>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(selectedStation?.slots ?? []).map((slot) => (
            <div key={slot.id} className="rounded-lg border border-quantum-border bg-quantum-panel p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-white">{slot.address}</div>
                  <div className="mt-1 text-xs uppercase tracking-[0.18em] text-red-300/70">{slot.type}</div>
                </div>
                <span className={`inline-flex rounded-full border px-3 py-1 text-xs font-semibold ${stateClass(slot.state)}`}>
                  {slot.state.toUpperCase()}
                </span>
              </div>
              <div className="mt-4 text-sm text-slate-300">
                Barcode <span className="font-semibold text-red-100">{slot.barcode ?? 'Empty'}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {openDoorMutation.isError ? <ErrorMessage error={openDoorMutation.error} /> : null}
      {closeDoorMutation.isError ? <ErrorMessage error={closeDoorMutation.error} /> : null}
      {importMutation.isError ? <ErrorMessage error={importMutation.error} /> : null}
      {exportMutation.isError ? <ErrorMessage error={exportMutation.error} /> : null}
    </div>
  );
}
