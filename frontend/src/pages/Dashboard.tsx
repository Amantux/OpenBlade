import { AlertTriangle, CassetteTape, CheckCircle2, DatabaseZap, ServerCog } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { getCartridges } from '../api/cartridges';
import DrivePanel from '../components/library/DrivePanel';
import SlotGrid from '../components/library/SlotGrid';
import ErrorMessage from '../components/ui/ErrorMessage';
import Card from '../components/ui/Card';
import Spinner from '../components/ui/Spinner';
import StatusPill from '../components/ui/StatusPill';
import { useHealth } from '../hooks/useHealth';
import { useInventory } from '../hooks/useInventory';
import { useJobs } from '../hooks/useJobs';
import { deriveSystemHealth, formatBytes, formatDate } from '../lib/utils';

export default function Dashboard() {
  const healthQuery = useHealth();
  const inventoryQuery = useInventory();
  const jobsQuery = useJobs();
  const cartridgesQuery = useQuery({ queryKey: ['cartridges'], queryFn: getCartridges, refetchInterval: 30_000 });

  if (healthQuery.isLoading || inventoryQuery.isLoading || jobsQuery.isLoading || cartridgesQuery.isLoading) {
    return <Spinner />;
  }

  if (healthQuery.isError) {
    return <ErrorMessage error={healthQuery.error} onRetry={() => healthQuery.refetch()} />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }
  if (cartridgesQuery.isError) {
    return <ErrorMessage error={cartridgesQuery.error} onRetry={() => cartridgesQuery.refetch()} />;
  }

  const inventory = inventoryQuery.data ?? { library_id: 'unknown', slots: [], drives: [], changer_state: 'unknown' };
  const jobs = jobsQuery.data ?? [];
  const health = healthQuery.health;
  const cartridges = cartridgesQuery.data ?? [];
  const systemHealth = deriveSystemHealth(health, inventory.drives, inventory.changer_state);
  const activeJobs = jobs.filter((job) => ['PENDING', 'RUNNING'].includes(job.status));
  const attentionItems = [] as string[];
  if (inventory.drives.some((drive) => ['FAULTED', 'OFFLINE'].includes(drive.drive_state))) {
    attentionItems.push('One or more drives require operator intervention.');
  }
  if (jobs.some((job) => job.status === 'FAILED')) {
    attentionItems.push('Failed jobs are waiting for review.');
  }
  if (inventory.drives.every((drive) => !drive.loaded)) {
    attentionItems.push('No loaded drives are available for archive workloads.');
  }
  if (attentionItems.length === 0) {
    attentionItems.push('No active alerts. System telemetry is nominal.');
  }
  const usedCapacity = cartridges.reduce((sum, cartridge) => sum + cartridge.used_bytes, 0);
  const totalCapacity = cartridges.reduce((sum, cartridge) => sum + cartridge.capacity_bytes, 0);

  return (
    <div className="space-y-6">
      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-slate-400">System Health</p>
              <div className="mt-3"><StatusPill status={systemHealth} /></div>
            </div>
            <CheckCircle2 className="h-5 w-5 text-emerald-400" />
          </div>
        </Card>
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-slate-400">Active Jobs</p>
              <p className="mt-3 text-3xl font-semibold text-white">{activeJobs.length}</p>
            </div>
            <DatabaseZap className="h-5 w-5 text-blue-400" />
          </div>
        </Card>
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-slate-400">Drive Status</p>
              <p className="mt-3 text-3xl font-semibold text-white">
                {inventory.drives.filter((drive) => drive.loaded).length}/{inventory.drives.length}
              </p>
              <p className="mt-1 text-sm text-slate-400">loaded with media</p>
            </div>
            <CassetteTape className="h-5 w-5 text-sky-400" />
          </div>
        </Card>
        <Card>
          <div className="flex items-start justify-between">
            <div>
              <p className="text-sm text-slate-400">Backend Mode</p>
              <p className="mt-3 text-3xl font-semibold text-white">{health?.backend ?? 'unknown'}</p>
              <p className="mt-1 text-sm text-slate-400">Capacity {formatBytes(usedCapacity)} / {formatBytes(totalCapacity)}</p>
            </div>
            <ServerCog className="h-5 w-5 text-violet-400" />
          </div>
        </Card>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1.4fr,1fr]">
        <Card className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Running jobs</h2>
              <p className="text-sm text-slate-400">Live activity across archive and restore operations.</p>
            </div>
            <span className="text-sm text-slate-500">{activeJobs.length} active</span>
          </div>
          <div className="overflow-hidden rounded-xl border border-slate-800">
            <table className="min-w-full divide-y divide-slate-800 text-sm">
              <thead className="bg-slate-900/70 text-left text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Job</th>
                  <th className="px-4 py-3 font-medium">Type</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Updated</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {(activeJobs.length ? activeJobs : jobs.slice(0, 5)).map((job) => (
                  <tr key={job.id}>
                    <td className="px-4 py-3 font-mono text-xs text-slate-200">{job.id.slice(0, 8)}</td>
                    <td className="px-4 py-3 text-slate-300">{job.job_type}</td>
                    <td className="px-4 py-3 text-slate-300">{job.status}</td>
                    <td className="px-4 py-3 text-slate-400">{formatDate(job.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="space-y-5">
          <div>
            <h2 className="text-lg font-semibold text-white">Library mini-map</h2>
            <p className="text-sm text-slate-400">Topology overview of slots and drive bays.</p>
          </div>
          <SlotGrid slots={inventory.slots} />
          <DrivePanel drives={inventory.drives} />
        </Card>
      </section>

      <section>
        <div className="mb-3 flex items-center gap-2 text-sm text-slate-400">
          <AlertTriangle className="h-4 w-4" /> Alert rail
        </div>
        <div className="grid gap-3 lg:grid-cols-3">
          {attentionItems.map((item) => (
            <Card key={item} className="border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-200">
              {item}
            </Card>
          ))}
        </div>
      </section>
    </div>
  );
}
