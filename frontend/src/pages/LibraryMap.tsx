import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';
import { getSlotTone, normalizeDrive, normalizeSlot, type NormalizedDrive, type NormalizedSlot } from '../lib/lmc';
import { useLibraryScope } from '../lib/useLibraryScope';

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

function driveBadge(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (['FAULTED', 'FAILED', 'OFFLINE'].includes(state)) return 'red';
  if (['BUSY', 'MOUNTING', 'UNMOUNTING', 'LOADING', 'UNLOADING'].includes(state)) return 'amber';
  if (['IDLE', 'READY', 'EMPTY'].includes(state)) return 'green';
  return 'blue';
}

export default function LibraryMap() {
  const navigate = useNavigate();
  const { libraryId, libraryName } = useLibraryScope();
  const inventoryQuery = useInventory(libraryId);
  const [selectedPartitionId, setSelectedPartitionId] = useState<string>();

  const inventory = inventoryQuery.data ?? { library_id: 'LIBRARY-01', slots: [], drives: [], changer_state: 'UNKNOWN' };
  const slots = useMemo(
    () => inventory.slots.map(normalizeSlot).sort((left, right) => left.element - right.element),
    [inventory.slots],
  );
  const drives = useMemo(() => inventory.drives.map(normalizeDrive), [inventory.drives]);

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

  const robotSummary = {
    state: inventory.changer_state ?? 'UNKNOWN',
    loadedElements: slots.filter((slot) => slot.occupied).length,
    ieSlots: slots.filter((slot) => slot.isIeArea).length,
    cleaningSlots: slots.filter((slot) => slot.isCleaning).length,
  };

  if (inventoryQuery.isLoading) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <span className="rounded border border-quantum-border bg-quantum-panel px-2 py-1">
          Library: <span className="font-medium text-slate-200">{libraryName || 'Primary Tape Library'}</span>
        </span>
        <Link to="/libraries" className="text-blue-400 hover:underline">Switch</Link>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.3fr,0.9fr]">
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
                  <span>{magazineSlots.filter((slot) => slot.occupied).length}/{magazineSlots.length}</span>
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

        <Card className="bg-quantum-info">
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Robotics</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Changer / Robot State</h2>
          <div className="mt-4 grid gap-3">
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Robot State</div>
              <div className="mt-2 text-2xl font-semibold text-slate-100">{robotSummary.state}</div>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Loaded Elements</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{robotSummary.loadedElements}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">IE Slots</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{robotSummary.ieSlots}</div>
              </div>
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Cleaning Slots</div>
                <div className="mt-2 text-lg font-semibold text-slate-100">{robotSummary.cleaningSlots}</div>
              </div>
            </div>
          </div>
        </Card>
      </div>

      <Card className="bg-quantum-north">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Drive Locations</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-100">Installed Drives</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {drives.map((drive: NormalizedDrive) => (
            <div key={drive.serialNumber} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-slate-100">{drive.serialNumber}</div>
                  <div className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-500">{drive.type}</div>
                </div>
                <Badge variant={driveBadge(drive.state)}>{drive.state}</Badge>
              </div>
              <div className="mt-3 text-sm text-slate-300">Mount: {drive.mountState}</div>
              <div className="mt-1 text-sm text-slate-400">Media: {drive.barcode ?? 'Empty'}</div>
            </div>
          ))}
        </div>
      </Card>

      <NorthPanel
        title="Partition Map"
        subtitle="Logical views of the physical library layout."
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
        subtitle="Selected section of the physical library."
        items={[
          { label: 'Element Count', value: selectedPartition?.elements ?? '—' },
          { label: 'Loaded Media', value: selectedPartition?.loaded ?? '—' },
          { label: 'Magazine Count', value: selectedPartition?.magazines ?? '—' },
          { label: 'Partition State', value: selectedPartition?.state ?? '—' },
          { label: 'Drives Installed', value: drives.length },
          { label: 'Robot State', value: robotSummary.state },
        ]}
      />

      <OperationsPanel
        title="Library Map Operations"
        subtitle="Refresh the physical view or jump into related workflows."
        actions={[
          { label: 'Refresh', onClick: () => void inventoryQuery.refetch(), variant: 'primary' },
          { label: 'Drive Operations', onClick: () => void navigate('/drives'), variant: 'secondary' },
          { label: 'Inventory', onClick: () => void navigate('/library/inventory'), variant: 'secondary' },
          { label: 'Archive', onClick: () => void navigate('/archive'), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
