import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useHealth } from '../hooks/useHealth';
import { useInventory } from '../hooks/useInventory';
import { useJobs } from '../hooks/useJobs';
import {
  buildRasTickets,
  getJobState,
  getSlotTone,
  normalizeDrive,
  normalizeSlot,
  type NormalizedSlot,
} from '../lib/lmc';
import { formatDate } from '../lib/utils';

interface PartitionRow {
  id: string;
  partition: string;
  elements: number;
  loaded: number;
  magazines: number;
  state: string;
}

function slotClasses(tone: ReturnType<typeof getSlotTone>, isIeArea: boolean): string {
  const base = 'rounded-sm border px-2 py-1 text-[11px] font-semibold';
  const toneClasses = {
    gray: 'border-slate-600 bg-slate-800 text-slate-300',
    green: 'border-emerald-700 bg-emerald-900/30 text-emerald-200',
    blue: 'border-sky-700 bg-sky-900/30 text-sky-200',
    red: 'border-red-700 bg-red-900/40 text-red-200',
  }[tone];

  return `${base} ${toneClasses} ${isIeArea ? 'ring-1 ring-amber-400/80' : ''}`;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const healthQuery = useHealth();
  const inventoryQuery = useInventory();
  const jobsQuery = useJobs();
  const [selectedPartitionId, setSelectedPartitionId] = useState<string>();

  const inventory = inventoryQuery.data ?? { library_id: 'LIBRARY-01', slots: [], drives: [], changer_state: 'UNKNOWN' };
  const health = healthQuery.health;
  const jobs = jobsQuery.data ?? [];
  const slots = inventory.slots.map(normalizeSlot).sort((left, right) => left.element - right.element);
  const drives = inventory.drives.map(normalizeDrive);
  const rasTickets = buildRasTickets(health, inventory, jobs);
  const activeJobs = jobs.filter((job) => ['PENDING', 'RUNNING'].includes(getJobState(job)));

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

  const magazines = useMemo(() => {
    const grouped = new Map<number, NormalizedSlot[]>();
    for (const slot of slots) {
      const current = grouped.get(slot.magazine) ?? [];
      current.push(slot);
      grouped.set(slot.magazine, current);
    }
    return Array.from(grouped.entries()).sort((left, right) => left[0] - right[0]);
  }, [slots]);

  const summaryCards = [
    { label: 'Total Elements', value: health?.slots_total ?? slots.length },
    { label: 'Elements Used', value: health?.slots_used ?? slots.filter((slot) => slot.occupied).length },
    { label: 'Drives Online', value: drives.filter((drive) => !['FAULTED', 'OFFLINE', 'FAILED'].includes(drive.state)).length },
    { label: 'Active Jobs', value: activeJobs.length },
  ];

  if (healthQuery.isLoading || inventoryQuery.isLoading || jobsQuery.isLoading) {
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

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-[1.3fr,0.9fr]">
        <div className="space-y-4">
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

          <Card className="bg-quantum-north">
            <div className="flex items-center justify-between">
              <div>
                <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Physical Layout</div>
                <h2 className="mt-1 text-lg font-semibold text-slate-100">Magazine / Element Map</h2>
              </div>
              <div className="flex flex-wrap gap-2 text-xs text-slate-400">
                <Badge variant="gray">Empty</Badge>
                <Badge variant="green">Loaded</Badge>
                <Badge variant="blue">Drive Busy</Badge>
                <Badge variant="red">Error</Badge>
              </div>
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-4">
              {magazines.map(([magazine, magazineSlots]) => (
                <div key={magazine} className="rounded-md border border-quantum-border bg-quantum-panel p-3">
                  <div className="mb-3 flex items-center justify-between text-xs uppercase tracking-[0.18em] text-slate-400">
                    <span>Magazine {magazine}</span>
                    <span>{magazineSlots.filter((slot) => slot.occupied).length}/5</span>
                  </div>
                  <div className="space-y-2">
                    {magazineSlots.map((slot) => (
                      <div key={slot.element} className={slotClasses(getSlotTone(slot), slot.isIeArea)}>
                        <div className="flex items-center justify-between gap-2">
                          <span>Element {slot.element}</span>
                          {slot.isCleaning ? <span>🧹</span> : null}
                        </div>
                        <div className="mt-1 truncate text-[10px] font-normal uppercase tracking-[0.14em] text-slate-300">
                          {slot.barcode ?? 'Empty'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>

        <Card className="bg-quantum-info">
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">RAS Summary</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Recent RAS Tickets</h2>
          <div className="mt-4 space-y-3">
            {rasTickets.map((ticket) => (
              <div key={ticket.id} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-slate-100">Severity {ticket.severity}</span>
                  <span className="text-xs uppercase tracking-[0.14em] text-slate-500">{ticket.component}</span>
                </div>
                <p className="mt-2 text-sm text-slate-300">{ticket.message}</p>
                <p className="mt-2 text-xs text-slate-500">{formatDate(ticket.time)}</p>
              </div>
            ))}
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
          { label: 'Inventory', onClick: () => void inventoryQuery.refetch(), variant: 'primary' },
          { label: 'View Inventory', onClick: () => void navigate('/library/inventory'), variant: 'secondary' },
          { label: 'Open Archive', onClick: () => void navigate('/archive'), variant: 'secondary' },
          { label: 'View Jobs', onClick: () => void navigate('/jobs'), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
