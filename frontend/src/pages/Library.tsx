import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';
import { useLibraryScope } from '../lib/useLibraryScope';
import { getSlotTone, normalizeSlot, type NormalizedSlot } from '../lib/lmc';

function toneToBadge(slot: NormalizedSlot): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (getSlotTone(slot)) {
    case 'green':
      return 'green';
    case 'blue':
      return 'blue';
    case 'red':
      return 'red';
    default:
      return 'gray';
  }
}

export default function Library() {
  const { libraryId, libraryName } = useLibraryScope();
  const inventoryQuery = useInventory(libraryId);
  const [selectedElement, setSelectedElement] = useState<string>();

  const inventory = inventoryQuery.data ?? { library_id: 'LIBRARY-01', slots: [], drives: [], changer_state: 'UNKNOWN' };
  const slots = useMemo(
    () => inventory.slots.map(normalizeSlot).sort((left, right) => left.element - right.element),
    [inventory.slots],
  );

  useEffect(() => {
    if (!selectedElement && slots.length > 0) {
      setSelectedElement(String(slots[0].element));
    }
  }, [selectedElement, slots]);

  const selectedSlot = slots.find((slot) => String(slot.element) === selectedElement) ?? slots[0];

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
      <NorthPanel
        title="Library Inventory"
        subtitle="Element inventory with IE Area and cleaning media annotations."
        columns={[
          {
            key: 'element',
            header: 'Element',
            render: (row: NormalizedSlot) => (
              <div className="flex items-center gap-2">
                <span>E{String(row.element).padStart(2, '0')}</span>
                {row.isCleaning ? <span title="Cleaning Slot">🧹</span> : null}
                {row.isIeArea ? <Badge variant="amber">IE Area</Badge> : null}
              </div>
            ),
          },
          { key: 'barcode', header: 'Barcode', render: (row: NormalizedSlot) => row.barcode ?? 'Empty' },
          {
            key: 'state',
            header: 'State',
            render: (row: NormalizedSlot) => <Badge variant={toneToBadge(row)}>{row.state}</Badge>,
          },
          { key: 'drive', header: 'Drive', render: (row: NormalizedSlot) => (row.driveId !== null ? `Drive ${row.driveId}` : '—') },
          { key: 'magazine', header: 'Magazine', render: (row: NormalizedSlot) => row.magazine },
        ]}
        rows={slots}
        getRowId={(row) => String(row.element)}
        selectedId={selectedElement}
        onSelect={(row) => setSelectedElement(String(row.element))}
      />

      <InformationPanel
        title={selectedSlot ? `Element ${selectedSlot.element}` : 'Element Details'}
        subtitle="Detailed view of the selected library element."
        items={[
          { label: 'Barcode', value: selectedSlot?.barcode ?? 'Empty' },
          { label: 'State', value: selectedSlot?.state ?? '—' },
          { label: 'Assigned Drive', value: selectedSlot?.driveId !== null ? `Drive ${selectedSlot?.driveId}` : 'None' },
          { label: 'Magazine', value: selectedSlot?.magazine ?? '—' },
          { label: 'IE Area', value: selectedSlot?.isIeArea ? 'Yes' : 'No' },
          { label: 'Cleaning Slot', value: selectedSlot?.isCleaning ? 'Yes' : 'No' },
        ]}
      />

      <OperationsPanel
        title="Inventory Operations"
        subtitle="Import and export actions are enabled when an IE Area element is selected."
        actions={[
          {
            label: 'Import',
            onClick: () => undefined,
            disabled: !selectedSlot?.isIeArea,
            variant: 'primary',
          },
          {
            label: 'Export',
            onClick: () => undefined,
            disabled: !selectedSlot?.isIeArea,
            variant: 'secondary',
          },
          {
            label: 'Inventory',
            onClick: () => void inventoryQuery.refetch(),
            variant: 'secondary',
          },
        ]}
      />
    </div>
  );
}
