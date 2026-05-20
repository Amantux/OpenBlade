import { useMemo, useRef } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  activateSystemFirmware,
  getDrives,
  getEthBlades,
  getFcBlades,
  getMgmtBlades,
  getSystemFirmware,
  getSystemFirmwareStatus,
  getSystemUpdates,
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
  values.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  return Array.from(counts.entries()).map(([version, count]) => `${version} (${count})`).join(', ');
}

export default function SystemFirmware() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const firmwareQuery = useQuery({ queryKey: ['system', 'firmware'], queryFn: getSystemFirmware, refetchInterval: 30_000 });
  const statusQuery = useQuery({ queryKey: ['system', 'firmware', 'status'], queryFn: getSystemFirmwareStatus, refetchInterval: 5_000 });
  const updatesQuery = useQuery({ queryKey: ['system', 'updates'], queryFn: getSystemUpdates, refetchInterval: 60_000 });
  const drivesQuery = useQuery({ queryKey: ['system', 'firmware', 'drives'], queryFn: getDrives, refetchInterval: 60_000 });
  const ethBladesQuery = useQuery({ queryKey: ['system', 'firmware', 'eth-blades'], queryFn: getEthBlades, refetchInterval: 60_000 });
  const fcBladesQuery = useQuery({ queryKey: ['system', 'firmware', 'fc-blades'], queryFn: getFcBlades, refetchInterval: 60_000 });
  const mgmtBladesQuery = useQuery({ queryKey: ['system', 'firmware', 'mgmt-blades'], queryFn: getMgmtBlades, refetchInterval: 60_000 });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadSystemFirmware(file),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware', 'status'] }),
      ]);
    },
  });

  const activateMutation = useMutation({
    mutationFn: activateSystemFirmware,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'firmware', 'status'] }),
      ]);
    },
  });

  if ([firmwareQuery, statusQuery, updatesQuery, drivesQuery, ethBladesQuery, fcBladesQuery, mgmtBladesQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [firmwareQuery, statusQuery, updatesQuery, drivesQuery, ethBladesQuery, fcBladesQuery, mgmtBladesQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const firmware = firmwareQuery.data!;
  const status = statusQuery.data!;
  const updates = updatesQuery.data ?? [];
  const driveVersions = summarizeVersions((drivesQuery.data ?? []).map((item) => item.firmware));
  const bladeVersions = summarizeVersions([
    ...(ethBladesQuery.data ?? []).map((item) => item.firmware),
    ...(fcBladesQuery.data ?? []).map((item) => item.firmware),
    ...(mgmtBladesQuery.data ?? []).map((item) => item.firmware),
  ]);
  const progress = Math.max(0, Math.min(100, status.progress));

  const cards = useMemo(
    () => [
      { label: 'System', value: firmware.currentVersion, detail: firmware.lastActivated ? `Activated ${formatDate(firmware.lastActivated)}` : 'No activation history' },
      { label: 'Drives', value: driveVersions || 'Unknown', detail: `${drivesQuery.data?.length ?? 0} drives tracked` },
      {
        label: 'Blades',
        value: bladeVersions || 'Unknown',
        detail: `${(ethBladesQuery.data?.length ?? 0) + (fcBladesQuery.data?.length ?? 0) + (mgmtBladesQuery.data?.length ?? 0)} blades tracked`,
      },
    ],
    [bladeVersions, driveVersions, drivesQuery.data?.length, ethBladesQuery.data?.length, fcBladesQuery.data?.length, firmware.currentVersion, firmware.lastActivated, mgmtBladesQuery.data?.length],
  );

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Firmware</h1>
            <p className="mt-2 text-sm text-slate-400">Track current component firmware, staged packages, and update progress from AML firmware routes.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  uploadMutation.mutate(file);
                }
                event.target.value = '';
              }}
            />
            <Button variant="secondary" onClick={() => fileInputRef.current?.click()}>{uploadMutation.isPending ? 'Uploading…' : 'Upload Firmware'}</Button>
            <Button disabled={!firmware.stagedVersion || activateMutation.isPending} onClick={() => activateMutation.mutate()}>{activateMutation.isPending ? 'Activating…' : 'Activate Staged Update'}</Button>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
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
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Update Progress</div>
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
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Available Updates</div>
          <div className="mt-4 space-y-3">
            {updates.length === 0 ? (
              <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No package updates are currently advertised.</div>
            ) : (
              updates.map((item) => (
                <div key={item.name} className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-4 text-sm text-slate-300">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="font-semibold text-slate-100">{item.name}</div>
                      <div className="mt-1 text-xs uppercase tracking-[0.16em] text-slate-500">{item.type}</div>
                    </div>
                    <Badge variant="blue">{item.version}</Badge>
                  </div>
                  <div className="mt-3">{item.description}</div>
                  <div className="mt-2 text-xs text-slate-500">{formatBytes(item.size)}</div>
                </div>
              ))
            )}
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Uploaded Firmware Packages</div>
          <div className="mt-4 space-y-3">
            {(firmware.uploadedPackages ?? []).map((item) => (
              <div key={item.name} className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-4 text-sm text-slate-300">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-100">{item.name}</div>
                    <div className="mt-1 text-xs text-slate-500">Uploaded {formatDate(item.uploadedAt)}</div>
                  </div>
                  <Badge variant={item.active ? 'green' : firmware.stagedPackage === item.name ? 'amber' : 'gray'}>{item.active ? 'Active' : firmware.stagedPackage === item.name ? 'Staged' : 'Available'}</Badge>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                  <span>Version {item.version}</span>
                  <span>{formatBytes(item.size)}</span>
                  <span>{item.checksum ?? 'No checksum'}</span>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {uploadMutation.isError ? <ErrorMessage error={uploadMutation.error} /> : null}
      {activateMutation.isError ? <ErrorMessage error={activateMutation.error} /> : null}
    </div>
  );
}
