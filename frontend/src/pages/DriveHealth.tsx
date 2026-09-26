import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../api/client';
import { getDriveHealth, type DriveHealth as DriveHealthRecord, type TapeAlertSeverity } from '../api/driveHealth';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import type { BadgeVariant } from '../lib/utils';

const SEVERITY_VARIANT: Record<TapeAlertSeverity, BadgeVariant> = {
  critical: 'red',
  warning: 'amber',
  information: 'blue',
  // Set on the wire but unclassified by the backend's spec table. Never rendered
  // as "fine": an operator still needs to see that the drive raised it.
  unknown: 'purple',
};

function severityVariant(severity: TapeAlertSeverity | null): BadgeVariant {
  return severity ? SEVERITY_VARIANT[severity] : 'green';
}

function DriveCard({ drive }: { drive: DriveHealthRecord }) {
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="font-mono text-sm font-semibold text-white">{drive.device}</div>
          <div className="mt-1 text-sm text-slate-400">
            {drive.inquiry.vendor} {drive.inquiry.product} · rev {drive.inquiry.revision}
          </div>
          <div className="mt-1 text-xs text-slate-500">
            Serial {drive.inquiry.serial || '(none reported)'} · type {drive.inquiry.deviceType}
          </div>
        </div>
        <Badge variant={severityVariant(drive.worstSeverity)}>
          {drive.worstSeverity ? `TapeAlert ${drive.worstSeverity}` : 'No flags set'}
        </Badge>
      </div>

      {!drive.tapeAlertSupported ? (
        <p className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-400">
          This drive does not implement the TapeAlert log page
          {drive.tapeAlertReason ? `: ${drive.tapeAlertReason}` : '.'} That is a property of the
          drive, not a failure.
        </p>
      ) : drive.activeFlags.length === 0 ? (
        <p className="mt-4 rounded-md border border-emerald-500/20 bg-emerald-500/5 p-4 text-sm text-emerald-100/80">
          No TapeAlert flags set ({drive.flagsRead} flags read).
        </p>
      ) : (
        <table className="mt-4 w-full text-left text-sm">
          <thead>
            <tr className="text-xs uppercase tracking-[0.18em] text-slate-500">
              <th className="pb-2 pr-4 font-semibold">Flag</th>
              <th className="pb-2 pr-4 font-semibold">Name</th>
              <th className="pb-2 font-semibold">Severity</th>
            </tr>
          </thead>
          <tbody>
            {drive.activeFlags.map((flag) => (
              <tr key={`${flag.number ?? 'unknown'}-${flag.name}`} className="border-t border-quantum-border">
                <td className="py-2 pr-4 font-mono text-slate-300">{flag.number ?? '?'}</td>
                <td className="py-2 pr-4 text-slate-200">{flag.name}</td>
                <td className="py-2">
                  <Badge variant={SEVERITY_VARIANT[flag.severity]}>{flag.severity}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export default function DriveHealth() {
  const [deviceInput, setDeviceInput] = useState('');
  const [device, setDevice] = useState('');

  const healthQuery = useQuery({
    queryKey: ['drive-health', device],
    queryFn: () => getDriveHealth(device || undefined),
    retry: false,
  });

  const error = healthQuery.error;
  // 503 is the documented "this deployment has no real drives" answer, not a
  // fault: the guard refuses unless both hardware variables are set.
  const hardwareDisabled = error instanceof ApiError && error.status === 503;
  const drives = healthQuery.data?.drives ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <p className="text-xs uppercase tracking-[0.22em] text-red-300/70">Drives</p>
            <h1 className="text-2xl font-semibold text-white">Drive Health</h1>
            <p className="max-w-3xl text-sm text-slate-400">
              SCSI inquiry data and TapeAlert flags, read straight from the drives. Read-only: this
              page issues an INQUIRY and one LOG SENSE per drive and changes nothing.
            </p>
          </div>
          <Button variant="secondary" onClick={() => void healthQuery.refetch()}>
            Refresh
          </Button>
        </div>
      </Card>

      <Card>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="flex-1">
            <label className="block text-sm text-slate-300" htmlFor="drive-health-device">
              Device (optional)
            </label>
            <input
              id="drive-health-device"
              value={deviceInput}
              onChange={(event) => setDeviceInput(event.target.value)}
              placeholder="/dev/nst0 — leave blank for every discovered drive"
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </div>
          <Button onClick={() => setDevice(deviceInput.trim())}>Inspect</Button>
        </div>
      </Card>

      {healthQuery.isLoading ? <Spinner /> : null}

      {hardwareDisabled ? (
        <Card>
          <h2 className="text-lg font-semibold text-slate-100">Real hardware is not enabled</h2>
          <p className="mt-2 text-sm text-slate-400">
            Drive health reads a physical drive, so there is nothing to report on a simulator
            backend. Enable it deliberately:
          </p>
          <pre className="mt-3 overflow-x-auto rounded-md border border-quantum-border bg-quantum-panel p-4 font-mono text-xs text-slate-300">
            OPENBLADE_BACKEND=real{'\n'}OPENBLADE_REAL_HARDWARE_ENABLED=true
          </pre>
          <p className="mt-3 text-xs text-slate-500">Reported by the backend: {error.message}</p>
        </Card>
      ) : healthQuery.isError ? (
        <ErrorMessage error={error} onRetry={() => healthQuery.refetch()} />
      ) : null}

      {!healthQuery.isLoading && !healthQuery.isError && drives.length === 0 ? (
        <Card>
          <h2 className="text-lg font-semibold text-slate-100">No tape drives discovered</h2>
          <p className="mt-2 text-sm text-slate-400">
            The backend found no drive to interrogate. Name a device above to inspect one directly.
          </p>
        </Card>
      ) : null}

      {drives.map((drive) => (
        <DriveCard key={drive.device} drive={drive} />
      ))}
    </div>
  );
}
