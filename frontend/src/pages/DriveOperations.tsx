import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { type Drive, listDrives } from '../api/drives';
import {
  listActiveJobs,
  queueDriveCleaning,
  queueDriveIdentify,
  queueDrivePerformanceTest,
  queueDrivePowerCycle,
  type OperationJobReceipt,
} from '../api/operations';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function jobVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (state === 'FAILED') return 'red';
  if (state === 'COMPLETED') return 'green';
  if (state === 'RUNNING') return 'blue';
  return 'amber';
}

function driveState(drive?: Drive): string {
  if (!drive) return 'UNKNOWN';
  if (String(drive.status).toUpperCase() === 'OFFLINE') return 'OFFLINE';
  return String(drive.state).toUpperCase();
}

export default function DriveOperations() {
  const queryClient = useQueryClient();
  const [selectedSerialNumber, setSelectedSerialNumber] = useState<string>();
  const [lastReceipt, setLastReceipt] = useState<OperationJobReceipt | null>(null);
  const drivesQuery = useQuery({ queryKey: ['drives'], queryFn: listDrives, refetchInterval: 30_000 });
  const jobsQuery = useQuery({ queryKey: ['operations', 'jobs', 'active'], queryFn: listActiveJobs, refetchInterval: 5_000 });

  const drives = drivesQuery.data ?? [];
  useEffect(() => {
    if (!selectedSerialNumber && drives.length > 0) {
      setSelectedSerialNumber(drives[0].serialNumber);
    }
  }, [drives, selectedSerialNumber]);

  const selectedDrive = drives.find((drive) => drive.serialNumber === selectedSerialNumber) ?? drives[0];
  const selectedBarcode = selectedDrive?.loadedMedia?.barcode ?? null;

  const refreshQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['drives'] }),
      queryClient.invalidateQueries({ queryKey: ['operations', 'jobs', 'active'] }),
      queryClient.invalidateQueries({ queryKey: ['jobs'] }),
    ]);
  };

  const operationMutation = useMutation({
    mutationFn: async (kind: 'clean' | 'identify' | 'power-cycle' | 'performance') => {
      switch (kind) {
        case 'clean':
          return queueDriveCleaning(selectedDrive ? [selectedDrive.serialNumber] : []);
        case 'identify':
          return queueDriveIdentify();
        case 'power-cycle':
          return queueDrivePowerCycle();
        case 'performance':
          return queueDrivePerformanceTest(selectedBarcode ? [selectedBarcode] : []);
      }
    },
    onSuccess: async (receipt) => {
      setLastReceipt(receipt);
      await refreshQueries();
    },
  });

  const queryError = drivesQuery.error ?? jobsQuery.error;
  const recentDriveJobs = useMemo(
    () => (jobsQuery.data ?? []).filter((job) => ['clean', 'audit', 'calibrate', 'verify'].includes(job.type)),
    [jobsQuery.data],
  );

  if (drivesQuery.isLoading || jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void drivesQuery.refetch();
      void jobsQuery.refetch();
    }} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Drive Operations</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Queued drive actions</h1>
            <p className="mt-2 text-sm text-slate-400">
              Drive operations are queued as jobs instead of invoking hardware controls directly. Clean, identify,
              reinitialize, and verification actions all surface through the job queue.
            </p>
          </div>
          <Badge variant="blue">{recentDriveJobs.length} active job{recentDriveJobs.length === 1 ? '' : 's'}</Badge>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Select Drive</div>
          <div className="mt-4 space-y-3">
            {drives.map((drive) => {
              const selected = drive.serialNumber === selectedDrive?.serialNumber;
              return (
                <button
                  key={drive.serialNumber}
                  type="button"
                  onClick={() => setSelectedSerialNumber(drive.serialNumber)}
                  className={`w-full rounded-md border px-4 py-4 text-left transition ${selected ? 'border-quantum-red bg-quantum-panel' : 'border-quantum-border bg-quantum-sidebar hover:bg-quantum-panel'}`}
                >
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-slate-100">{drive.serialNumber}</div>
                      <div className="mt-1 text-sm text-slate-400">{drive.type}</div>
                    </div>
                    <Badge variant={jobVariant(driveState(drive))}>{driveState(drive)}</Badge>
                  </div>
                  <div className="mt-3 text-sm text-slate-300">Loaded tape: {drive.loadedMedia?.barcode ?? 'Empty'}</div>
                </button>
              );
            })}
          </div>
        </Card>

        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Action Queue</div>
              <div className="mt-1 text-lg font-semibold text-slate-100">{selectedDrive?.serialNumber ?? 'No drive selected'}</div>
            </div>
            <Badge variant="gray">Jobs only</Badge>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <Button disabled={!selectedDrive || operationMutation.isPending} onClick={() => operationMutation.mutate('clean')}>
              {operationMutation.isPending ? 'Queuing…' : 'Clean'}
            </Button>
            <Button variant="secondary" disabled={!selectedDrive || operationMutation.isPending} onClick={() => operationMutation.mutate('identify')}>
              Identify
            </Button>
            <Button variant="secondary" disabled={!selectedDrive || operationMutation.isPending} onClick={() => operationMutation.mutate('power-cycle')}>
              Power Cycle
            </Button>
            <Button variant="secondary" disabled={!selectedDrive || !selectedBarcode || operationMutation.isPending} onClick={() => operationMutation.mutate('performance')}>
              Performance Test
            </Button>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4 text-sm text-slate-300">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">State</div><div className="mt-1 text-slate-100">{driveState(selectedDrive)}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Loaded Tape</div><div className="mt-1 text-slate-100">{selectedBarcode ?? 'None'}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Firmware</div><div className="mt-1 text-slate-100">{selectedDrive?.firmware ?? '—'}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Mode</div><div className="mt-1 text-slate-100">Queued control-plane action</div></div>
          </div>
          {lastReceipt?.jobId ? (
            <div className="mt-4 rounded-md border border-emerald-700 bg-emerald-900/20 px-4 py-3 text-sm text-emerald-200">
              Queued job <Link className="font-mono underline" to="/jobs">{lastReceipt.jobId}</Link> for {selectedDrive?.serialNumber}.
            </div>
          ) : null}
          {operationMutation.isError ? <div className="mt-4"><ErrorMessage error={operationMutation.error} /></div> : null}
        </Card>
      </div>

      <Card>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Recent Drive Jobs</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">Job-backed operations</div>
          </div>
          <Button variant="secondary" onClick={() => void jobsQuery.refetch()}>Refresh</Button>
        </div>
        <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full text-sm">
            <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
              <tr>
                <th className="px-4 py-3">Job</th>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3">Barcode</th>
                <th className="px-4 py-3">Started</th>
              </tr>
            </thead>
            <tbody>
              {recentDriveJobs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-400">No queued drive operations yet.</td>
                </tr>
              ) : recentDriveJobs.map((job, index) => (
                <tr key={job.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-200">{job.id}</td>
                  <td className="px-4 py-3 text-slate-300">{job.type}</td>
                  <td className="px-4 py-3"><Badge variant={jobVariant(job.state)}>{job.state}</Badge></td>
                  <td className="px-4 py-3 text-slate-300">{job.barcode ?? '—'}</td>
                  <td className="px-4 py-3 text-slate-300">{formatDate(job.startedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
