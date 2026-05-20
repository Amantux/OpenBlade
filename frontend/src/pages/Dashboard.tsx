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
import { buildRasTickets, getJobState, getSlotTone, normalizeDrive, normalizeSlot, type NormalizedSlot } from '../lib/lmc';
import { formatDate } from '../lib/utils';

interface PartitionRow {
  id: string;
  partition: string;
  elements: number;
  loaded: number;
  magazines: number;
  state: string;
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
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Operations Snapshot</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-100">Overview</h2>
            <div className="mt-4 grid gap-3 md:grid-cols-3">
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Robot State</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{inventory.changer_state ?? 'UNKNOWN'}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Loaded Media</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{slots.filter((slot) => slot.occupied).length}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Drive Attention</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">
                  {drives.filter((drive) => ['FAULTED', 'OFFLINE', 'FAILED'].includes(drive.state)).length}
                </div>
              </div>
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
          { label: 'Refresh', onClick: () => void Promise.all([healthQuery.refetch(), inventoryQuery.refetch(), jobsQuery.refetch()]), variant: 'primary' },
          { label: 'Physical Map', onClick: () => void navigate('/library'), variant: 'secondary' },
          { label: 'Open Archive', onClick: () => void navigate('/archive'), variant: 'secondary' },
          { label: 'Open Catalog', onClick: () => void navigate('/catalog'), variant: 'secondary' },
          { label: 'View Jobs', onClick: () => void navigate('/jobs'), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
