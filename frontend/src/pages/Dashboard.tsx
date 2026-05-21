import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { getCatalogStatus, getLibraryStatus, getPublicHealth } from '../api/catalogAdmin';
import { getAmlSummary, getDashboardStats } from '../api/dashboard';
import { getEvents, getRasTickets } from '../api/health';
import { listTapeOperations } from '../api/safety';
import { listHydrationJobs } from '../api/virtualFs';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';
import { useJobs } from '../hooks/useJobs';
import { getJobState, getJobTypeLabel, getSlotTone, normalizeDrive, normalizeSlot, type NormalizedSlot } from '../lib/lmc';
import { formatBytes, formatDate, getDriveStateVariant, toTitleCase } from '../lib/utils';

interface PartitionRow {
  id: string;
  partition: string;
  elements: number;
  loaded: number;
  magazines: number;
  state: string;
}

const EMPTY_INVENTORY = { slots: [], drives: [], changer_state: 'UNKNOWN' };

function ticketVariant(severity: string): 'green' | 'amber' | 'red' {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'red';
    case 'warning':
      return 'amber';
    default:
      return 'green';
  }
}

function eventVariant(severity: string): 'blue' | 'amber' | 'red' {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'red';
    case 'warning':
      return 'amber';
    default:
      return 'blue';
  }
}

function healthVariant(status?: string): 'green' | 'amber' | 'red' | 'gray' {
  switch ((status ?? '').toLowerCase()) {
    case 'ok':
      return 'green';
    case 'degraded':
      return 'amber';
    case 'unhealthy':
      return 'red';
    default:
      return 'gray';
  }
}

function tapeOpVariant(status: string): 'green' | 'blue' | 'red' | 'gray' {
  switch (status.toLowerCase()) {
    case 'completed':
      return 'green';
    case 'queued':
    case 'running':
      return 'blue';
    case 'failed':
      return 'red';
    default:
      return 'gray';
  }
}

export default function Dashboard() {
  const navigate = useNavigate();
  const inventoryQuery = useInventory();
  const jobsQuery = useJobs();
  const summaryQuery = useQuery({ queryKey: ['dashboard', 'summary'], queryFn: getAmlSummary, refetchInterval: 10_000 });
  const statsQuery = useQuery({ queryKey: ['dashboard', 'stats'], queryFn: getDashboardStats, refetchInterval: 30_000 });
  const ticketsQuery = useQuery({ queryKey: ['dashboard', 'tickets'], queryFn: getRasTickets, refetchInterval: 10_000 });
  const eventsQuery = useQuery({ queryKey: ['dashboard', 'events'], queryFn: () => getEvents(6), refetchInterval: 10_000 });
  const publicHealthQuery = useQuery({ queryKey: ['dashboard', 'public-health'], queryFn: getPublicHealth, refetchInterval: 30_000 });
  const catalogStatusQuery = useQuery({ queryKey: ['dashboard', 'catalog-status'], queryFn: getCatalogStatus, refetchInterval: 30_000 });
  const libraryStatusQuery = useQuery({ queryKey: ['dashboard', 'library-status'], queryFn: getLibraryStatus, refetchInterval: 30_000 });
  const recentTapeOpsQuery = useQuery({ queryKey: ['dashboard', 'recent-tape-ops'], queryFn: () => listTapeOperations(5), refetchInterval: 30_000 });
  const hydrationJobsQuery = useQuery({ queryKey: ['dashboard', 'hydration-jobs'], queryFn: listHydrationJobs, refetchInterval: 30_000 });
  const [selectedPartitionId, setSelectedPartitionId] = useState<string>();

  const inventory = inventoryQuery.data ?? EMPTY_INVENTORY;
  const jobs = jobsQuery.data ?? [];
  const summary = summaryQuery.data;
  const storage = statsQuery.data?.storage;
  const slots = inventory.slots.map(normalizeSlot).sort((left, right) => left.element - right.element);
  const drives = inventory.drives.map(normalizeDrive);
  const activeJobs = jobs.filter((job) => ['PENDING', 'RUNNING'].includes(getJobState(job)));
  const activeHydrationJobs = (hydrationJobsQuery.data ?? []).filter((job) => ['queued', 'running'].includes(job.status)).length;
  const recentTapeOps = recentTapeOpsQuery.data ?? [];

  const partitionRows = useMemo<PartitionRow[]>(() => {
    const ieArea = slots.filter((slot) => slot.isIeArea);
    const cleaning = slots.filter((slot) => slot.isCleaning);
    const standard = slots.filter((slot) => !slot.isIeArea && !slot.isCleaning);

    const buildRow = (id: string, partition: string, partitionSlots: NormalizedSlot[]): PartitionRow => ({
      id,
      partition,
      elements: partitionSlots.length,
      loaded: partitionSlots.filter((slot) => slot.occupied).length,
      magazines: new Set(partitionSlots.map((slot) => slot.magazine)).size,
      state: partitionSlots.some((slot) => getSlotTone(slot) === 'red') ? 'Attention' : 'Ready',
    });

    return [
      buildRow('partition-a', 'Library Partition A', standard),
      buildRow('ie-area', 'IE Area', ieArea),
      buildRow('cleaning', 'Cleaning Partition', cleaning),
    ];
  }, [slots]);

  useEffect(() => {
    if (!selectedPartitionId && partitionRows.length > 0) {
      setSelectedPartitionId(partitionRows[0].id);
    }
  }, [partitionRows, selectedPartitionId]);

  const selectedPartition = partitionRows.find((row) => row.id === selectedPartitionId) ?? partitionRows[0];
  const summaryCards = [
    { label: 'Drives Online', value: `${summary?.drives.online ?? 0}/${summary?.drives.total ?? drives.length}` },
    { label: 'Slot Utilization', value: `${summary?.slots.utilizationPercent ?? 0}%` },
    { label: 'Active Jobs', value: summary?.jobs.active ?? activeJobs.length },
    { label: 'Events Logged', value: summary?.events.total ?? (eventsQuery.data?.length ?? 0) },
  ];

  if (
    inventoryQuery.isLoading ||
    jobsQuery.isLoading ||
    summaryQuery.isLoading ||
    statsQuery.isLoading ||
    ticketsQuery.isLoading ||
    eventsQuery.isLoading
  ) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }
  if (summaryQuery.isError) {
    return <ErrorMessage error={summaryQuery.error} onRetry={() => summaryQuery.refetch()} />;
  }
  if (statsQuery.isError) {
    return <ErrorMessage error={statsQuery.error} onRetry={() => statsQuery.refetch()} />;
  }
  if (ticketsQuery.isError) {
    return <ErrorMessage error={ticketsQuery.error} onRetry={() => ticketsQuery.refetch()} />;
  }
  if (eventsQuery.isError) {
    return <ErrorMessage error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-4">
        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Health Status</div>
          {publicHealthQuery.isLoading ? <div className="mt-4 text-sm text-slate-400">Loading health…</div> : null}
          {publicHealthQuery.isError ? <div className="mt-4 text-sm text-red-300">Unable to load /healthz.</div> : null}
          {publicHealthQuery.data ? (
            <>
              <div className="mt-3 flex items-center justify-between gap-3">
                <div className="text-2xl font-semibold text-slate-100">{publicHealthQuery.data.status.toUpperCase()}</div>
                <Badge variant={healthVariant(publicHealthQuery.data.status)}>{publicHealthQuery.data.status.toUpperCase()}</Badge>
              </div>
              <div className="mt-2 text-sm text-slate-400">Checked {formatDate(publicHealthQuery.data.checked_at)}</div>
            </>
          ) : null}
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Quick Stats</div>
          {catalogStatusQuery.isLoading ? <div className="mt-4 text-sm text-slate-400">Loading catalog…</div> : null}
          {catalogStatusQuery.isError ? <div className="mt-4 text-sm text-red-300">Unable to load /status/catalog.</div> : null}
          {catalogStatusQuery.data ? (
            <div className="mt-4 grid gap-3 text-sm text-slate-300">
              <div className="flex items-center justify-between gap-3"><span>Total datasets</span><span className="text-white">{catalogStatusQuery.data.total_datasets}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Total files</span><span className="text-white">{catalogStatusQuery.data.total_file_records}</span></div>
            </div>
          ) : null}
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Library Status</div>
          {libraryStatusQuery.isLoading ? <div className="mt-4 text-sm text-slate-400">Loading library…</div> : null}
          {libraryStatusQuery.isError ? <div className="mt-4 text-sm text-red-300">Unable to load /status/library.</div> : null}
          {libraryStatusQuery.data ? (
            <div className="mt-4 grid gap-3 text-sm text-slate-300">
              <div className="flex items-center justify-between gap-3"><span>Drives loaded</span><span className="text-white">{libraryStatusQuery.data.cartridges_loaded}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Slots occupied</span><span className="text-white">{libraryStatusQuery.data.slots_occupied}</span></div>
            </div>
          ) : null}
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Hydration Queue</div>
          {hydrationJobsQuery.isLoading ? <div className="mt-4 text-sm text-slate-400">Loading jobs…</div> : null}
          {hydrationJobsQuery.isError ? <div className="mt-4 text-sm text-red-300">Unable to load /virtual/jobs.</div> : null}
          {!hydrationJobsQuery.isError ? (
            <>
              <div className="mt-3 flex items-center justify-between gap-3">
                <div className="text-3xl font-semibold text-slate-100">{activeHydrationJobs}</div>
                <Badge variant={activeHydrationJobs > 0 ? 'blue' : 'gray'}>{activeHydrationJobs > 0 ? 'Active' : 'Idle'}</Badge>
              </div>
              <div className="mt-2 text-sm text-slate-400">{hydrationJobsQuery.data?.length ?? 0} total hydration job(s).</div>
            </>
          ) : null}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Tape Operations</div>
              <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent Tape Operations</h2>
            </div>
            <Badge variant="blue">{recentTapeOps.length}</Badge>
          </div>
          <div className="mt-4 space-y-3">
            {recentTapeOpsQuery.isLoading ? <div className="text-sm text-slate-400">Loading tape operations…</div> : null}
            {recentTapeOpsQuery.isError ? <div className="text-sm text-red-300">Unable to load /tape-ops.</div> : null}
            {recentTapeOps.map((operation) => (
              <div key={operation.op_id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-100">{toTitleCase(operation.op_type)} · {operation.barcode || '—'}</div>
                    <div className="mt-1 text-sm text-slate-400">Drive {operation.drive_id ?? '—'} · {formatDate(operation.started_at ?? operation.created_at)}</div>
                  </div>
                  <Badge variant={tapeOpVariant(operation.status)}>{toTitleCase(operation.status)}</Badge>
                </div>
              </div>
            ))}
            {!recentTapeOpsQuery.isLoading && recentTapeOps.length === 0 && !recentTapeOpsQuery.isError ? (
              <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No recent tape operations.</div>
            ) : null}
          </div>
        </Card>

        <div className="grid gap-4">
          <Card className="bg-quantum-north">
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Library Overview</div>
            <div className="mt-3 grid gap-3 md:grid-cols-4">
              {summaryCards.map((card) => (
                <div key={card.label} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{card.label}</div>
                  <div className="mt-3 text-2xl font-semibold text-slate-100">{card.value}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="bg-quantum-info">
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">RAS Summary</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent RAS Tickets</h2>
            <div className="mt-4 space-y-3">
              {(ticketsQuery.data ?? []).slice(0, 6).map((ticket) => (
                <div key={ticket.id} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <Badge variant={ticketVariant(ticket.severity)}>{ticket.severity}</Badge>
                    <span className="text-xs uppercase tracking-[0.14em] text-slate-500">{ticket.component}</span>
                  </div>
                  <p className="mt-2 text-sm text-slate-300">{ticket.message}</p>
                  <p className="mt-2 text-xs text-slate-500">{formatDate(ticket.opened)}</p>
                </div>
              ))}
              {(ticketsQuery.data ?? []).length === 0 ? (
                <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">
                  No open RAS tickets.
                </div>
              ) : null}
            </div>
          </Card>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.3fr,0.9fr]">
        <div className="space-y-4">
          <Card className="bg-quantum-north">
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Operations Snapshot</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-100">Overview</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Robot State</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{inventory.changer_state ?? 'UNKNOWN'}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Loaded Media</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{summary?.slots.used ?? slots.filter((slot) => slot.occupied).length}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Drive Attention</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{summary?.drives.attention ?? 0}</div>
              </div>
            </div>
          </Card>
        </div>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Hydration Queue</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Restore Activity</h2>
          <div className="mt-4 space-y-3">
            {(hydrationJobsQuery.data ?? []).slice(0, 5).map((job) => (
              <div key={job.job_id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate font-semibold text-slate-100">{job.paths[0] ?? job.job_id}</div>
                    <div className="mt-1 text-sm text-slate-400">{job.completed_files}/{job.total_files} files</div>
                  </div>
                  <Badge variant={job.status === 'completed' ? 'green' : job.status === 'failed' ? 'red' : 'blue'}>{job.status.toUpperCase()}</Badge>
                </div>
              </div>
            ))}
            {(hydrationJobsQuery.data ?? []).length === 0 ? (
              <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No hydration jobs reported.</div>
            ) : null}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Drive Status</div>
          <div className="mt-4 space-y-3">
            {drives.slice(0, 6).map((drive) => (
              <div key={drive.serialNumber} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-100">{drive.serialNumber}</div>
                    <div className="mt-1 text-sm text-slate-400">{drive.type} · {drive.tapeLoaded ? (drive.barcode ?? 'Loaded') : 'Empty'}</div>
                  </div>
                  <Badge variant={getDriveStateVariant(drive.state)}>{drive.state}</Badge>
                </div>
              </div>
            ))}
            {drives.length === 0 ? <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No drive data available.</div> : null}
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Slot Utilization</div>
          <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
            <div className="flex items-end justify-between gap-3">
              <div>
                <div className="text-3xl font-semibold text-slate-100">{summary?.slots.used ?? 0}/{summary?.slots.total ?? 0}</div>
                <div className="mt-1 text-sm text-slate-400">Occupied elements across partitions and IE slots.</div>
              </div>
              <Badge variant={(summary?.slots.utilizationPercent ?? 0) > 90 ? 'amber' : 'blue'}>{summary?.slots.utilizationPercent ?? 0}% used</Badge>
            </div>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-900">
              <div className="h-full bg-quantum-red" style={{ width: `${summary?.slots.utilizationPercent ?? 0}%` }} />
            </div>
            <div className="mt-4 grid gap-3 text-sm text-slate-300 md:grid-cols-3">
              <div>Standard: {partitionRows[0]?.elements ?? 0}</div>
              <div>IE: {partitionRows[1]?.elements ?? 0}</div>
              <div>Cleaning: {partitionRows[2]?.elements ?? 0}</div>
            </div>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Storage Capacity</div>
          <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
            <div className="text-3xl font-semibold text-slate-100">{formatBytes(storage?.totalBytes ?? 0)}</div>
            <div className="mt-1 text-sm text-slate-400">Cataloged archive footprint across {storage?.volumeGroupCount ?? 0} volume groups.</div>
            <div className="mt-4 grid gap-3 text-sm text-slate-300">
              <div className="flex items-center justify-between gap-3"><span>Catalog files</span><span>{storage?.totalFiles ?? 0}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Assigned tapes</span><span>{storage?.totalAssignedTapes ?? 0}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Catalog tapes</span><span>{storage?.totalCatalogTapes ?? 0}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Tape utilization</span><span>{storage?.utilizationPercent ?? 0}%</span></div>
            </div>
          </div>
          <div className="mt-4 space-y-2">
            {(statsQuery.data?.volumeGroups ?? []).slice(0, 3).map((group) => (
              <div key={group.id} className="flex items-center justify-between gap-3 rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-300">
                <span>{group.name}</span>
                <span>{formatBytes(group.storedBytes)}</span>
              </div>
            ))}
            {(statsQuery.data?.volumeGroups ?? []).length === 0 ? <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No cataloged storage data yet.</div> : null}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Jobs</div>
              <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent Jobs</h2>
            </div>
            <Badge variant="blue">{jobs.length}</Badge>
          </div>
          <div className="mt-4 space-y-3">
            {jobs.slice(0, 5).map((job) => (
              <div key={job.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-slate-100">{getJobTypeLabel(job)}</div>
                    <div className="mt-1 text-sm text-slate-400">{job.id}</div>
                  </div>
                  <Badge variant={getJobState(job) === 'FAILED' ? 'red' : getJobState(job) === 'RUNNING' ? 'blue' : 'gray'}>{getJobState(job)}</Badge>
                </div>
                <div className="mt-2 text-sm text-slate-300">Updated {formatDate(job.updated_at)}</div>
              </div>
            ))}
            {jobs.length === 0 ? <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No jobs reported by the backend.</div> : null}
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Events</div>
              <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent Events</h2>
            </div>
            <Badge variant="blue">{summary?.events.total ?? 0}</Badge>
          </div>
          <div className="mt-4 space-y-3">
            {(eventsQuery.data ?? []).map((event) => (
              <div key={event.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="font-semibold text-slate-100">{event.component}</div>
                    <div className="mt-1 text-sm text-slate-300">{event.message}</div>
                  </div>
                  <Badge variant={eventVariant(event.severity)}>{event.severity}</Badge>
                </div>
                <div className="mt-2 text-sm text-slate-400">{formatDate(event.timestamp)}</div>
              </div>
            ))}
            {(eventsQuery.data ?? []).length === 0 ? <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">No recent events.</div> : null}
          </div>
        </Card>
      </div>

      <NorthPanel
        title="Partition Summary"
        subtitle="Logical partitions and magazine assignments for the active library frame."
        columns={[
          { key: 'partition', header: 'Partition', render: (row: PartitionRow) => row.partition },
          { key: 'elements', header: 'Elements', render: (row: PartitionRow) => row.elements },
          { key: 'loaded', header: 'Loaded', render: (row: PartitionRow) => row.loaded },
          { key: 'magazines', header: 'Magazines', render: (row: PartitionRow) => row.magazines },
          {
            key: 'state',
            header: 'State',
            render: (row: PartitionRow) => <Badge variant={row.state === 'Attention' ? 'amber' : 'green'}>{row.state}</Badge>,
          },
        ]}
        rows={partitionRows}
        getRowId={(row) => row.id}
        selectedId={selectedPartition?.id}
        onSelect={(row) => setSelectedPartitionId(row.id)}
      />

      <InformationPanel
        title={selectedPartition?.partition ?? 'Partition Details'}
        subtitle="Selected partition details and operator guidance."
        items={[
          { label: 'Element Count', value: selectedPartition?.elements ?? '—' },
          { label: 'Loaded Media', value: selectedPartition?.loaded ?? '—' },
          { label: 'Magazine Count', value: selectedPartition?.magazines ?? '—' },
          { label: 'Partition State', value: selectedPartition?.state ?? '—' },
        ]}
      />

      <OperationsPanel
        title="Library Operations"
        subtitle="Quick actions commonly used from the overview screen."
        actions={[
          {
            label: 'Refresh',
            onClick: () => void Promise.all([
              inventoryQuery.refetch(),
              jobsQuery.refetch(),
              summaryQuery.refetch(),
              statsQuery.refetch(),
              ticketsQuery.refetch(),
              eventsQuery.refetch(),
              publicHealthQuery.refetch(),
              catalogStatusQuery.refetch(),
              libraryStatusQuery.refetch(),
              recentTapeOpsQuery.refetch(),
              hydrationJobsQuery.refetch(),
            ]),
            variant: 'primary',
          },
          { label: 'Physical Map', onClick: () => void navigate('/library'), variant: 'secondary' },
          { label: 'Open Archive', onClick: () => void navigate('/archive'), variant: 'secondary' },
          { label: 'Open Catalog', onClick: () => void navigate('/catalog'), variant: 'secondary' },
          { label: 'View Jobs', onClick: () => void navigate('/jobs'), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
