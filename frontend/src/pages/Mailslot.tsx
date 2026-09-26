import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import {
  exportThroughMailslot,
  importFromMailslot,
  listMailslotSlots,
  previewMailslotExport,
  type ExportAssessment,
  type MailslotMoveResult,
} from '../api/mailslot';
import ExportConfirmDialog from '../components/mailslot/ExportConfirmDialog';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes } from '../lib/utils';

const SLOTS_QUERY_KEY = ['mailslot', 'slots'];

function MoveResultPanel({ result }: { result: MailslotMoveResult }) {
  return (
    <div className="mt-4 rounded-md border border-emerald-500/30 bg-emerald-500/10 p-4 text-sm text-emerald-100">
      <div className="font-semibold">
        {result.source === 'import_export_slot' ? 'Imported' : 'Exported'} {result.barcode}
      </div>
      <p className="mt-1 text-emerald-100/80">
        {result.source === 'import_export_slot'
          ? `I/E slot ${result.sourceSlot} → storage slot ${result.destinationSlot}`
          : `Storage slot ${result.sourceSlot} → I/E slot ${result.destinationSlot}`}
        {result.destinationSlotChosen ? ' (slot chosen automatically)' : ''}
      </p>
      <p className="mt-1 font-mono text-xs text-emerald-100/60">operation {result.opId}</p>
    </div>
  );
}

function AssessmentPanel({ assessment }: { assessment: ExportAssessment }) {
  if (!assessment.carriesData) {
    return (
      <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-300">
        <div className="font-semibold text-slate-100">
          {assessment.barcode} carries no archived data
        </div>
        <p className="mt-1 text-slate-400">
          Nothing becomes unrestorable by exporting it. It can leave the library as-is.
        </p>
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
      <div className="font-semibold">{assessment.barcode} still carries archived data</div>
      <ul className="mt-2 space-y-1 text-amber-100/90">
        <li>{assessment.archivedFilesOnCartridge} archived file instance(s)</li>
        {assessment.pendingFilesOnCartridge > 0 ? (
          <li>{assessment.pendingFilesOnCartridge} write(s) still in flight to it</li>
        ) : null}
        <li>{formatBytes(assessment.bytesOnCartridge)} on the cartridge</li>
        {assessment.volumeGroup ? <li>Volume group {assessment.volumeGroup}</li> : null}
      </ul>
      {assessment.samplePaths.length > 0 ? (
        <ul className="mt-2 space-y-1 font-mono text-xs text-amber-100/80">
          {assessment.samplePaths.map((path) => (
            <li key={path}>{path}</li>
          ))}
        </ul>
      ) : null}
      {assessment.volumeGroupBarcodesWithData.length > 0 ? (
        <p className="mt-2 text-xs text-amber-100/70">
          Other cartridges in that group hold data too:{' '}
          {assessment.volumeGroupBarcodesWithData.join(', ')}
        </p>
      ) : null}
      <p className="mt-3 text-amber-100/80">
        The API refuses this export. Exporting anyway needs an explicit confirmation.
      </p>
    </div>
  );
}

export default function Mailslot() {
  const queryClient = useQueryClient();
  const [sourceIeSlot, setSourceIeSlot] = useState<number>();
  const [targetSlotMode, setTargetSlotMode] = useState<'auto' | 'manual'>('auto');
  const [targetSlot, setTargetSlot] = useState('');
  const [exportBarcode, setExportBarcode] = useState('');
  const [confirmBarcode, setConfirmBarcode] = useState<string>();
  // The barcode the SERVER refused. A typed confirmBarcode may only ever be offered for this one:
  // the refusal panel outlives an edit of the field, and reading the field at click
  // time would arm the dialog for a cartridge nothing has assessed.
  const [refusedBarcode, setRefusedBarcode] = useState<string>();

  const slotsQuery = useQuery({
    queryKey: SLOTS_QUERY_KEY,
    queryFn: listMailslotSlots,
    refetchInterval: 10_000,
  });

  const previewMutation = useMutation({
    mutationFn: (barcode: string) => previewMailslotExport(barcode),
  });

  const importMutation = useMutation({
    mutationFn: () =>
      importFromMailslot(
        sourceIeSlot!,
        targetSlotMode === 'manual' && targetSlot.trim() ? Number(targetSlot) : undefined,
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: SLOTS_QUERY_KEY });
    },
  });

  const exportMutation = useMutation({
    mutationFn: ({ barcode, confirmBarcode }: { barcode: string; confirmBarcode?: string }) =>
      exportThroughMailslot(barcode, { confirmBarcode }),
    onSuccess: async () => {
      setConfirmBarcode(undefined);
      setRefusedBarcode(undefined);
      await queryClient.invalidateQueries({ queryKey: SLOTS_QUERY_KEY });
    },
    onError: (error, variables) => {
      setRefusedBarcode(
        error instanceof ApiError && error.status === 409 ? variables.barcode : undefined,
      );
    },
  });

  function changeExportBarcode(next: string) {
    setExportBarcode(next);
    // Editing the field invalidates every answer the server gave about the old
    // one — including the refusal that unlocks the typed confirmation.
    setRefusedBarcode(undefined);
    setConfirmBarcode(undefined);
    exportMutation.reset();
  }

  if (slotsQuery.isLoading) {
    return <Spinner />;
  }
  if (slotsQuery.isError) {
    return <ErrorMessage error={slotsQuery.error} onRetry={() => slotsQuery.refetch()} />;
  }

  const listing = slotsQuery.data ?? {
    importExportSlots: [],
    slotCount: 0,
    occupiedCount: 0,
    supported: false,
  };
  const occupiedSlots = listing.importExportSlots.filter((slot) => slot.occupied);
  // An assessment is only an answer about the barcode it was fetched for: once the
  // field changes, a stale panel would describe a different cartridge.
  const assessment =
    previewMutation.data && previewMutation.data.barcode === exportBarcode.trim()
      ? previewMutation.data
      : null;
  const exportError = exportMutation.error;
  const exportRefused =
    exportError instanceof ApiError &&
    exportError.status === 409 &&
    refusedBarcode === exportBarcode.trim();

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.22em] text-red-300/70">Library</p>
            <h1 className="text-2xl font-semibold text-white">Mailslot</h1>
            <p className="max-w-3xl text-sm text-slate-400">
              The import/export station: bring a cartridge in from an I/E element, or send one out.
              Exports that would take archived data out of the library are refused until you confirm
              them by barcode.
            </p>
          </div>
          <Button variant="secondary" onClick={() => void slotsQuery.refetch()}>
            Refresh
          </Button>
        </div>
      </Card>

      <Card>
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Station</p>
            <h2 className="mt-2 text-xl font-semibold text-white">Import/export elements</h2>
          </div>
          <span className="text-sm text-slate-400">
            {listing.occupiedCount} of {listing.slotCount} occupied
          </span>
        </div>

        {!listing.supported ? (
          <p className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-400">
            This library backend has no import/export station, so there is nothing to import from
            and nowhere to export to.
          </p>
        ) : listing.slotCount === 0 ? (
          <p className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-400">
            This library reports no import/export elements.
          </p>
        ) : (
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {listing.importExportSlots.map((slot) => (
              <div
                key={slot.slotId}
                className="rounded-lg border border-quantum-border bg-quantum-panel p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="text-sm font-semibold text-white">I/E slot {slot.slotId}</div>
                  <Badge variant={slot.occupied ? 'blue' : 'gray'}>
                    {slot.occupied ? 'Occupied' : 'Empty'}
                  </Badge>
                </div>
                <div className="mt-4 text-sm text-slate-300">
                  Barcode{' '}
                  <span className="font-mono font-semibold text-red-100">
                    {slot.barcode ?? '—'}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Import</p>
          <h2 className="mt-2 text-xl font-semibold text-white">Bring a cartridge in</h2>

          {occupiedSlots.length === 0 ? (
            <p className="mt-4 text-sm text-slate-400">
              No I/E element holds a cartridge. Load one into the station first.
            </p>
          ) : (
            <div className="mt-4 space-y-4">
              <div>
                <label className="block text-sm text-slate-300" htmlFor="import-source-slot">
                  Source I/E element
                </label>
                <select
                  id="import-source-slot"
                  value={sourceIeSlot ?? ''}
                  onChange={(event) =>
                    // The placeholder option's value is '', and Number('') is 0 —
                    // a real element id, which would fire an import for I/E slot 0.
                    setSourceIeSlot(event.target.value === '' ? undefined : Number(event.target.value))
                  }
                  className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                >
                  <option value="">Select an element…</option>
                  {occupiedSlots.map((slot) => (
                    <option key={slot.slotId} value={slot.slotId}>
                      I/E slot {slot.slotId} — {slot.barcode}
                    </option>
                  ))}
                </select>
              </div>

              <fieldset className="space-y-2">
                <legend className="text-sm text-slate-300">Destination storage slot</legend>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input
                    type="radio"
                    name="target-slot-mode"
                    value="auto"
                    checked={targetSlotMode === 'auto'}
                    onChange={() => setTargetSlotMode('auto')}
                  />
                  Automatic — the first empty storage slot
                </label>
                <label className="flex items-center gap-2 text-sm text-slate-300">
                  <input
                    type="radio"
                    name="target-slot-mode"
                    value="manual"
                    checked={targetSlotMode === 'manual'}
                    onChange={() => setTargetSlotMode('manual')}
                  />
                  Target slot
                </label>
                {targetSlotMode === 'manual' ? (
                  <input
                    aria-label="Target storage slot"
                    inputMode="numeric"
                    value={targetSlot}
                    onChange={(event) => setTargetSlot(event.target.value.replace(/\D/g, ''))}
                    placeholder="Slot number"
                    className="w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                  />
                ) : null}
              </fieldset>

              <Button
                disabled={
                  sourceIeSlot === undefined ||
                  importMutation.isPending ||
                  (targetSlotMode === 'manual' && !targetSlot.trim())
                }
                onClick={() => importMutation.mutate()}
              >
                {importMutation.isPending ? 'Importing…' : 'Import cartridge'}
              </Button>
            </div>
          )}

          {importMutation.isError ? (
            <div className="mt-4">
              <ErrorMessage error={importMutation.error} />
            </div>
          ) : null}
          {importMutation.data ? <MoveResultPanel result={importMutation.data} /> : null}
        </Card>

        <Card>
          <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Export</p>
          <h2 className="mt-2 text-xl font-semibold text-white">Send a cartridge out</h2>

          <div className="mt-4 space-y-4">
            <div>
              <label className="block text-sm text-slate-300" htmlFor="export-barcode">
                Cartridge barcode
              </label>
              <input
                id="export-barcode"
                value={exportBarcode}
                onChange={(event) => changeExportBarcode(event.target.value)}
                autoComplete="off"
                placeholder="e.g. PHO001L8"
                className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-quantum-red"
              />
            </div>

            <div className="flex flex-wrap gap-3">
              <Button
                variant="secondary"
                disabled={!exportBarcode.trim() || previewMutation.isPending}
                onClick={() => previewMutation.mutate(exportBarcode.trim())}
              >
                {previewMutation.isPending ? 'Checking…' : 'Check what would leave'}
              </Button>
              <Button
                disabled={!exportBarcode.trim() || exportMutation.isPending}
                onClick={() =>
                  exportMutation.mutate({ barcode: exportBarcode.trim() })
                }
              >
                {exportMutation.isPending && !confirmBarcode ? 'Exporting…' : 'Export cartridge'}
              </Button>
            </div>
          </div>

          {previewMutation.isError ? (
            <div className="mt-4">
              <ErrorMessage error={previewMutation.error} />
            </div>
          ) : null}
          {assessment ? <AssessmentPanel assessment={assessment} /> : null}

          {exportMutation.isError ? (
            <div className="mt-4 space-y-3">
              <ErrorMessage
                error={exportMutation.error}
                title={exportRefused ? 'Export refused' : undefined}
              />
              {exportRefused ? (
                <Button
                  variant="danger"
                  onClick={() => {
                    // `refusedBarcode`, never the input field: this must be the
                    // cartridge the server actually refused.
                    const barcode = refusedBarcode!;
                    // The dialog will not enable confirm without an assessment;
                    // fetch it if the operator went straight for Export.
                    if (!assessment || assessment.barcode !== barcode) {
                      previewMutation.mutate(barcode);
                    }
                    setConfirmBarcode(barcode);
                  }}
                >
                  Force export…
                </Button>
              ) : null}
            </div>
          ) : null}
          {exportMutation.data ? <MoveResultPanel result={exportMutation.data} /> : null}
        </Card>
      </div>

      <ExportConfirmDialog
        open={Boolean(confirmBarcode)}
        barcode={confirmBarcode ?? ''}
        assessment={
          previewMutation.data && previewMutation.data.barcode === confirmBarcode
            ? previewMutation.data
            : null
        }
        isAssessmentPending={previewMutation.isPending}
        onRetryAssessment={() => previewMutation.mutate(confirmBarcode!)}
        isProcessing={exportMutation.isPending}
        onConfirm={(typed) => exportMutation.mutate({ barcode: confirmBarcode!, confirmBarcode: typed })}
        onCancel={() => setConfirmBarcode(undefined)}
      />
    </div>
  );
}
