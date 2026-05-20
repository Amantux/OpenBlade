import { useQuery } from '@tanstack/react-query';
import { getCartridges, getMediaPools } from '../api/cartridges';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import type { CartridgeResponse, MediaPoolResponse } from '../types/api';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (state.toLowerCase().includes('error')) {
    return 'red';
  }
  if (state.toLowerCase().includes('mounted')) {
    return 'blue';
  }
  return 'gray';
}

function derivePool(cartridge: CartridgeResponse, pools: MediaPoolResponse[]): string {
  const byType = pools.find((pool) => pool.type === cartridge.type);
  return byType?.name ?? cartridge.partition ?? 'unassigned';
}

export default function Media() {
  const mediaQuery = useQuery({ queryKey: ['media'], queryFn: getCartridges, refetchInterval: 30_000 });
  const poolsQuery = useQuery({ queryKey: ['media', 'pools'], queryFn: getMediaPools, refetchInterval: 60_000 });

  const media = mediaQuery.data ?? [];
  const pools = poolsQuery.data ?? [];
  const selected = media[0];

  if (mediaQuery.isLoading || poolsQuery.isLoading) {
    return <Spinner />;
  }

  if (mediaQuery.isError) {
    return <ErrorMessage error={mediaQuery.error} onRetry={() => mediaQuery.refetch()} />;
  }

  if (poolsQuery.isError) {
    return <ErrorMessage error={poolsQuery.error} onRetry={() => poolsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <NorthPanel
        title="Cartridges"
        subtitle="AML media inventory with pool and location mapping."
        columns={[
          { key: 'barcode', header: 'Barcode', render: (row: CartridgeResponse) => row.barcode },
          { key: 'type', header: 'Type', render: (row: CartridgeResponse) => row.type },
          { key: 'pool', header: 'Pool', render: (row: CartridgeResponse) => derivePool(row, pools) },
          { key: 'location', header: 'Location', render: (row: CartridgeResponse) => row.slotAddress },
          {
            key: 'state',
            header: 'State',
            render: (row: CartridgeResponse) => <Badge variant={stateVariant(row.state)}>{row.state}</Badge>,
          },
        ]}
        rows={media}
        getRowId={(row) => row.barcode}
        emptyMessage="No cartridges were returned by the AML controller."
      />

      <InformationPanel
        title={selected?.barcode ?? 'Media Summary'}
        subtitle="Live cartridge inventory from /aml/media."
        items={[
          { label: 'Type', value: selected?.type ?? '—' },
          { label: 'Pool', value: selected ? derivePool(selected, pools) : '—' },
          { label: 'Partition', value: selected?.partition ?? '—' },
          { label: 'Location', value: selected?.slotAddress ?? '—' },
          { label: 'State', value: selected?.state ?? '—' },
          { label: 'Write Protected', value: selected?.writeProtected ? 'Yes' : 'No' },
          { label: 'WORM', value: selected?.worm ? 'Yes' : 'No' },
        ]}
      />
    </div>
  );
}
