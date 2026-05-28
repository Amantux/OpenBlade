import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { listActiveJobs } from '../api/operations';
import {
  activateSystemFirmware,
  getBladeFirmware,
  getDrives,
  getSystemFirmware,
  getSystemFirmwareStatus,
  uploadSystemFirmware,
} from '../api/system';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes, formatDate } from '../lib/utils';

function summarizeVersions(values: string[]): string {
  const counts = new Map<string, number>();
  values.filter(Boolean).forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return Array.from(counts.entries()).map(([version, count]) => `${version} (${count})`).join(', ');
}

export default function SystemFirmware() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [feedback, setFeedback] = useState<string>('');

  const firmwareQuery = useQuery({ queryKey: ['system', 'firmware'], queryFn: getSystemFirmware, refetchInterval: 30_000 });
  const statusQuery = useQuery({ queryKey: ['system', 'firmware', 'status'], queryFn: getSystemFirmwareStatus, refetchInterval: 5_000 });
  const drivesQuery = useQuery({ queryKey: ['system', 'firmware', 'drives'], queryFn: getDrives, refetchInterval: 60_000 });
  const bladeFirmwareQuery = useQuery({ queryKey: ['system', 'firmware', 'blades'], queryFn: getBladeFirmware, refetchInterval: 60_000 });
  const jobsQuery = useQuery({ queryKey: ['operations', 'jobs', 'active'], queryFn: listActiveJobs, refetchInterval: 5_000 });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadSystemFirmware(file),
    onSuccess: async () => {
      setFeedback('Firmware package uploaded and staged.');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware', 'status'] }),
      ]);
    },
  });

  const activateMutation = useMutation({
    mutationFn: activateSystemFirmware,
    onSuccess: async () => {
      setFeedback('Firmware activation completed.');
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware', 'status'] }),
      ]);
    },
  });

  if ([firmwareQuery, statusQuery, drivesQuery, bladeFirmwareQuery, jobsQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [firmwareQuery, statusQuery, drivesQuery, bladeFirmwareQuery, jobsQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const firmware = firmwareQuery.data!;
  const status = statusQuery.data!;
  const bladeFirmware = bladeFirmwareQuery.data ?? [];
  const driveVersions = summarizeVersions((drivesQuery.data ?? []).map((item) => item.firmware));
  const bladeVersions = summarizeVersions(bladeFirmware.map((item) => item.version));
  const stagedPackage = firmware.uploadedPackages.find((item) => item.name === firmware.stagedPackage);
  const progress = Math.max(0, Math.min(100, status.progress));
  const activeJobs = jobsQuery.data ?? [];
  const libraryBusy = activeJobs.length > 0;

  const cards = [
    { label: 'Library Firmware', value: firmware.currentVersion, detail: firmware.lastActivated ? `Activated ${formatDate(firmware.lastActivated)}` : 'No activation history' },
    { label: 'Blade Versions', value: bladeVersions || 'Unknown', detail: `${bladeFirmware.length} controller bundle(s)` },
    { label: 'Drive Versions', value: driveVersions || 'Unknown', detail: `${drivesQuery.data?.length ?? 0} drives tracked` },
    { label: 'Library Busy', value: libraryBusy ? 'Yes' : 'No', detail: libraryBusy ? `${activeJobs.length} active job(s)` : 'Safe to stage firmware' },
  ];

  const activity = [
    ...bladeFirmware.map((item) => ({
      id: `blade-${item.name}`,
      kind: 'Blade bundle',
      target: item.target,
      version: item.version,
      status: item.status,
      updatedAt: item.uploadedAt,
    })),
    ...(firmware.uploadedPackages ?? []).map((item) => ({
      id: `system-${item.name}`,
      kind: 'System package',
      target: item.name,
      version: item.version,
      status: item.active ? 'active' : firmware.stagedPackage === item.name ? 'staged' : 'uploaded',
      updatedAt: item.uploadedAt,
    })),
  ].sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Firmware</h1>
            <p className="mt-2 text-sm text-slate-400">
              View mock library firmware, stage uploads, and review recent firmware activity while blocking changes whenever the library is busy.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  setFeedback('');
                  uploadMutation.mutate(file);
                }
                event.target.value = '';
              }}
            />
            <Button variant="secondary" disabled={libraryBusy || uploadMutation.isPending || activateMutation.isPending} onClick={() => fileInputRef.current?.click()}>
              {uploadMutation.isPending ? 'Uploading…' : 'Upload Firmware'}
            </Button>
            <Button disabled={libraryBusy || !firmware.stagedVersion || activateMutation.isPending || uploadMutation.isPending} onClick={() => activateMutation.mutate()}>
              {activateMutation.isPending ? 'Activating…' : 'Activate Staged Update'}
            </Button>
          </div>
        </div>
        {libraryBusy ? <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-900/10 px-4 py-3 text-sm text-amber-200">Firmware changes are blocked while the library has active jobs.</div> : null}
        {feedback ? <div className="mt-4 rounded-md border border-emerald-700 bg-emerald-900/20 px-4 py-3 text-sm text-emerald-200">{feedback}</div> : null}
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <Card key={card.label}>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{card.label}</div>
            <div className="mt-2 text-lg font-semibold text-slate-100">{card.value}</div>
            <div className="mt-2 text-sm text-slate-400">{card.detail}</div>
          </Card>
        ))}
      </div>

      <Card>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Update Status</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">{status.message}</div>
          </div>
          <Badge variant={status.state === 'completed' ? 'green' : status.state === 'uploaded' ? 'amber' : 'blue'}>{status.state}</Badge>
        </div>
        <div className="mt-4 h-3 overflow-hidden rounded-full bg-slate-900">
          <div className="h-full bg-quantum-red" style={{ width: `${progress}%` }} />
        </div>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-sm text-slate-300">
          <span>{progress}% complete</span>
          <span>Current: {status.currentVersion}</span>
          <span>Staged: {status.stagedVersion ?? 'None'}</span>
          <span>Updated: {formatDate(status.lastUpdated)}</span>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Staged Package</div>
          <div className="mt-4 space-y-3">
            {stagedPackage ? (
              <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-4 text-sm text-slate-300">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-100">{stagedPackage.name}</div>
                    <div className="mt-1 text-xs text-slate-500">Uploaded {formatDate(stagedPackage.uploadedAt)}</div>
                  </div>
                  <Badge variant="amber">{stagedPackage.version}</Badge>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  <span>{formatBytes(stagedPackage.size)}</span>
                  <span>{stagedPackage.checksum ?? 'No checksum'}</span>
                  <span>Status: staged</span>
                </div>
              </div>
            ) : <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No staged package is waiting for activation.</div>}
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Recent Firmware Activity</div>
          <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3">Kind</th>
                  <th className="px-4 py-3">Target</th>
                  <th className="px-4 py-3">Version</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Updated</th>
                </tr>
              </thead>
              <tbody>
                {activity.length === 0 ? (
                  <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-400">No firmware activity recorded yet.</td></tr>
                ) : activity.map((item, index) => (
                  <tr key={item.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 text-slate-100">{item.kind}</td>
                    <td className="px-4 py-3 text-slate-300">{item.target}</td>
                    <td className="px-4 py-3 text-slate-300">{item.version}</td>
                    <td className="px-4 py-3"><Badge variant={item.status === 'active' ? 'green' : item.status === 'staged' ? 'amber' : 'blue'}>{item.status}</Badge></td>
                    <td className="px-4 py-3 text-slate-300">{formatDate(item.updatedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {uploadMutation.isError ? <ErrorMessage error={uploadMutation.error} /> : null}
      {activateMutation.isError ? <ErrorMessage error={activateMutation.error} /> : null}
    </div>
  );
}
