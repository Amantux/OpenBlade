import { useQuery } from '@tanstack/react-query';
import { getPartitions } from '../api/partitions';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import type { PartitionResponse } from '../types/api';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'online':
      return 'green';
    case 'offline':
      return 'red';
    default:
      return 'amber';
  }
}

export default function Partitions() {
  const partitionsQuery = useQuery({ queryKey: ['partitions'], queryFn: getPartitions, refetchInterval: 30_000 });
  const partitions = partitionsQuery.data ?? [];
  const selected = partitions[0];

  if (partitionsQuery.isLoading) {
    return <Spinner />;
  }

  if (partitionsQuery.isError) {
    return <ErrorMessage error={partitionsQuery.error} onRetry={() => partitionsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <NorthPanel
        title="Partitions"
        subtitle="Logical AML partition inventory with drive and slot allocation."
        columns={[
          { key: 'name', header: 'Name', render: (row: PartitionResponse) => row.name },
          { key: 'mode', header: 'Mode', render: (row: PartitionResponse) => row.type },
          { key: 'drives', header: 'Drives', render: (row: PartitionResponse) => row.driveCount },
          { key: 'slots', header: 'Slots', render: (row: PartitionResponse) => row.slotCount },
          {
            key: 'status',
            header: 'Status',
            render: (row: PartitionResponse) => <Badge variant={statusVariant(row.status)}>{row.status}</Badge>,
          },
        ]}
        rows={partitions}
        getRowId={(row) => row.id}
        emptyMessage="No partitions were returned by the AML controller."
      />

      <InformationPanel
        title={selected?.name ?? 'Partition Summary'}
        subtitle="The first partition is highlighted until interactive selection is added."
        items={[
          { label: 'Mode', value: selected?.type ?? '—' },
          { label: 'Drives', value: selected?.driveCount ?? '—' },
          { label: 'Slots', value: selected?.slotCount ?? '—' },
          { label: 'IE Slots', value: selected?.ieSlotCount ?? '—' },
          { label: 'Cleaning Slots', value: selected?.cleaningSlots ?? '—' },
          { label: 'Media Count', value: selected?.mediaCount ?? '—' },
          { label: 'Status', value: selected?.status ?? '—' },
        ]}
      />
    </div>
  );
}
