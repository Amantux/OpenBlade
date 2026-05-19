import type { DriveResponse, HealthResponse, InventoryResponse, SystemHealth } from '../../types/api';
import { getDriveStateVariant, getMountStateVariant } from '../../lib/utils';
import Badge from '../ui/Badge';
import Card from '../ui/Card';
import StatusPill from '../ui/StatusPill';
import ApiLatencyChart from './ApiLatencyChart';

interface HealthPanelProps {
  health?: HealthResponse;
  inventory?: InventoryResponse;
  status: SystemHealth;
  avgLatency: number;
  latencies: Array<{ index: number; latency: number }>;
}

function DriveHealthTable({ drives }: { drives: DriveResponse[] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-800">
      <table className="min-w-full divide-y divide-slate-800 text-sm">
        <thead className="bg-slate-900/70 text-left text-slate-400">
          <tr>
            <th className="px-4 py-3 font-medium">Drive</th>
            <th className="px-4 py-3 font-medium">Media</th>
            <th className="px-4 py-3 font-medium">Drive state</th>
            <th className="px-4 py-3 font-medium">Mount state</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {drives.map((drive) => (
            <tr key={drive.drive_id}>
              <td className="px-4 py-3 text-slate-200">Drive {drive.drive_id}</td>
              <td className="px-4 py-3 font-mono text-xs text-slate-400">{drive.barcode ?? 'Empty'}</td>
              <td className="px-4 py-3"><Badge variant={getDriveStateVariant(drive.drive_state)}>{drive.drive_state}</Badge></td>
              <td className="px-4 py-3"><Badge variant={getMountStateVariant(drive.mount_state)}>{drive.mount_state}</Badge></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function HealthPanel({ health, inventory, status, avgLatency, latencies }: HealthPanelProps) {
  return (
    <Card className="space-y-6">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">System overview</p>
          <div className="flex items-center gap-3">
            <StatusPill status={status} />
            <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-xs text-slate-300">
              Backend {health?.backend ?? 'unknown'}
            </span>
          </div>
          <p className="text-sm text-slate-400">Health endpoint status: {health?.status ?? 'unknown'}</p>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-4">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">API latency</p>
          <div className="mt-2 flex items-center gap-4">
            <ApiLatencyChart latencies={latencies} />
            <div>
              <p className="text-2xl font-semibold text-white">{avgLatency}ms</p>
              <p className="text-xs text-slate-400">rolling average</p>
            </div>
          </div>
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Library</p>
          <p className="mt-2 text-lg font-semibold text-white">{inventory?.library_id ?? '—'}</p>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Changer</p>
          <p className="mt-2 text-lg font-semibold text-white">{inventory?.changer_state ?? '—'}</p>
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900/40 p-4">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Drives</p>
          <p className="mt-2 text-lg font-semibold text-white">{inventory?.drives.length ?? 0}</p>
        </div>
      </div>
      <DriveHealthTable drives={inventory?.drives ?? []} />
    </Card>
  );
}
