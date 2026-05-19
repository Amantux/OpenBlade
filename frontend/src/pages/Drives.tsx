import { useEffect, useMemo, useState } from 'react';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';
import { normalizeDrive, type NormalizedDrive } from '../lib/lmc';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (['FAULTED', 'FAILED', 'OFFLINE'].includes(state)) {
    return 'red';
  }
  if (['BUSY', 'MOUNTING', 'UNMOUNTING'].includes(state)) {
    return 'amber';
  }
  return 'green';
}

export default function Drives() {
  const inventoryQuery = useInventory();
  const [selectedDriveId, setSelectedDriveId] = useState<string>();

  const drives = useMemo(() => (inventoryQuery.data?.drives ?? []).map(normalizeDrive), [inventoryQuery.data?.drives]);

  useEffect(() => {
    if (!selectedDriveId && drives.length > 0) {
      setSelectedDriveId(String(drives[0].id));
    }
  }, [drives, selectedDriveId]);

  const selectedDrive: NormalizedDrive | undefined = drives.find((drive) => String(drive.id) === selectedDriveId) ?? drives[0];

  if (inventoryQuery.isLoading) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <NorthPanel
        title="Drive Overview"
        subtitle="LTO-8 HH FC / SAS drive status, mount state, and current media placement."
        columns={[
          { key: 'drive', header: 'Drive', render: (row: NormalizedDrive) => `Drive ${row.id}` },
          { key: 'type', header: 'Type', render: (row: NormalizedDrive) => row.type },
          { key: 'state', header: 'State', render: (row: NormalizedDrive) => <Badge variant={stateVariant(row.state)}>{row.state}</Badge> },
          { key: 'loaded', header: 'Tape Loaded', render: (row: NormalizedDrive) => (row.tapeLoaded ? 'Yes' : 'No') },
          { key: 'barcode', header: 'Barcode', render: (row: NormalizedDrive) => row.barcode ?? 'Empty' },
        ]}
        rows={drives}
        getRowId={(row) => String(row.id)}
        selectedId={selectedDrive ? String(selectedDrive.id) : undefined}
        onSelect={(row) => setSelectedDriveId(String(row.id))}
        emptyMessage="No drives reported by the library controller."
      />

      <InformationPanel
        title={selectedDrive ? `Drive ${selectedDrive.id}` : 'Drive Details'}
        subtitle="Detailed status for the selected drive."
        items={[
          { label: 'Drive Type', value: selectedDrive?.type ?? '—' },
          { label: 'State', value: selectedDrive?.state ?? '—' },
          { label: 'Mount State', value: selectedDrive?.mountState ?? '—' },
          { label: 'Tape Loaded', value: selectedDrive?.tapeLoaded ? 'Yes' : 'No' },
          { label: 'Barcode', value: selectedDrive?.barcode ?? 'Empty' },
        ]}
      />

      <OperationsPanel
        title="Drive Operations"
        subtitle="Vary-On or Vary-Off controls are enabled according to the selected drive state."
        actions={[
          {
            label: 'Vary-On',
            onClick: () => undefined,
            disabled: !selectedDrive || !['OFFLINE', 'FAILED'].includes(selectedDrive.state),
            variant: 'primary',
          },
          {
            label: 'Vary-Off',
            onClick: () => undefined,
            disabled: !selectedDrive || ['OFFLINE', 'FAILED'].includes(selectedDrive.state),
            variant: 'secondary',
          },
          { label: 'Refresh', onClick: () => void inventoryQuery.refetch(), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
