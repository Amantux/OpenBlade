import { useEffect, useState } from 'react';
import type { ExportAssessment } from '../../api/mailslot';
import Button from '../ui/Button';
import { formatBytes } from '../../lib/utils';

interface ExportConfirmDialogProps {
  open: boolean;
  barcode: string;
  /** What the API says would leave with this cartridge. Null while unknown. */
  assessment: ExportAssessment | null;
  isProcessing?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The gate in front of a forced export.
 *
 * Exporting a cartridge that carries archived data makes every file on it
 * unrestorable until someone physically puts it back, and the API refuses unless
 * `force` is set. `force` therefore never travels on a single click: the operator
 * has to type the barcode, which is the same posture as the CLI's `--force` —
 * you cannot get there without naming the cartridge.
 */
export default function ExportConfirmDialog({
  open,
  barcode,
  assessment,
  isProcessing = false,
  onConfirm,
  onCancel,
}: ExportConfirmDialogProps) {
  const [typedBarcode, setTypedBarcode] = useState('');

  useEffect(() => {
    // A stale value from a previous cartridge must never satisfy this gate.
    setTypedBarcode('');
  }, [barcode, open]);

  if (!open) {
    return null;
  }

  const barcodeMatches = typedBarcode.trim() === barcode;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4">
      <div className="w-full max-w-lg rounded-lg border border-red-500/40 bg-quantum-info p-6 shadow-2xl">
        <div className="text-xs uppercase tracking-[0.2em] text-red-300/80">Data loss</div>
        <h2 className="mt-2 text-xl font-semibold text-slate-100">Force export of {barcode}?</h2>

        <p className="mt-3 text-sm text-slate-400">
          Exporting moves this cartridge out of the library. Every file instance on it becomes
          unrestorable until the cartridge is imported again.
        </p>

        {assessment ? (
          <dl className="mt-4 space-y-2 rounded-md border border-red-500/20 bg-red-950/20 p-4 text-sm text-red-100/90">
            <div className="flex justify-between gap-4">
              <dt>Archived file instances</dt>
              <dd className="font-semibold">{assessment.archivedFilesOnCartridge}</dd>
            </div>
            {assessment.pendingFilesOnCartridge > 0 ? (
              <div className="flex justify-between gap-4">
                <dt>Writes still in flight</dt>
                <dd className="font-semibold">{assessment.pendingFilesOnCartridge}</dd>
              </div>
            ) : null}
            <div className="flex justify-between gap-4">
              <dt>Bytes on cartridge</dt>
              <dd className="font-semibold">{formatBytes(assessment.bytesOnCartridge)}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt>Volume group</dt>
              <dd className="font-semibold">{assessment.volumeGroup ?? 'None'}</dd>
            </div>
            {assessment.samplePaths.length > 0 ? (
              <div>
                <dt className="mb-1">Files that leave with it</dt>
                <dd>
                  <ul className="space-y-1 font-mono text-xs">
                    {assessment.samplePaths.map((path) => (
                      <li key={path}>{path}</li>
                    ))}
                  </ul>
                  {assessment.archivedFilesOnCartridge > assessment.samplePaths.length ? (
                    <p className="mt-1 text-xs text-red-200/70">
                      +{assessment.archivedFilesOnCartridge - assessment.samplePaths.length} more
                    </p>
                  ) : null}
                </dd>
              </div>
            ) : null}
            {assessment.volumeGroupBarcodesWithData.length > 0 ? (
              <div>
                <dt className="mb-1">Other cartridges in that group holding data</dt>
                <dd className="font-mono text-xs">
                  {assessment.volumeGroupBarcodesWithData.join(', ')}
                </dd>
              </div>
            ) : null}
          </dl>
        ) : null}

        <label className="mt-5 block text-sm text-slate-300" htmlFor="force-export-barcode">
          Type the barcode <span className="font-mono text-red-200">{barcode}</span> to confirm
        </label>
        <input
          id="force-export-barcode"
          value={typedBarcode}
          onChange={(event) => setTypedBarcode(event.target.value)}
          autoComplete="off"
          placeholder="Barcode"
          className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-quantum-red"
        />

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={isProcessing} onClick={onCancel}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="danger"
            disabled={!barcodeMatches || isProcessing}
            onClick={onConfirm}
          >
            {isProcessing ? 'Exporting…' : 'Export anyway'}
          </Button>
        </div>
      </div>
    </div>
  );
}
