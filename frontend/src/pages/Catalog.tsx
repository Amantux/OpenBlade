import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { ApiError, apiRequest } from '../api/client';
import { getJobs } from '../api/jobs';
import { buildCatalogFallback } from '../lib/lmc';
import { formatBytes, formatDate } from '../lib/utils';
import type { CatalogEntryResponse, CatalogResponse } from '../types/api';

async function getCatalogEntries(): Promise<CatalogEntryResponse[]> {
  const candidates = ['/catalog', '/catalog/'] as const;

  for (const path of candidates) {
    try {
      const payload = await apiRequest<CatalogResponse | CatalogEntryResponse[]>(path);
      return Array.isArray(payload) ? payload : payload.entries;
    } catch (error) {
      if (!(error instanceof ApiError) || error.status !== 404) {
        throw error;
      }
    }
  }

  const jobs = await getJobs();
  return buildCatalogFallback(jobs);
}

export default function Catalog() {
  const catalogQuery = useQuery({ queryKey: ['catalog'], queryFn: getCatalogEntries, refetchInterval: 30_000 });
  const [selectedEntryId, setSelectedEntryId] = useState<string>();

  const entries = catalogQuery.data ?? [];

  useEffect(() => {
    if (!selectedEntryId && entries.length > 0) {
      setSelectedEntryId(entries[0].id);
    }
  }, [entries, selectedEntryId]);

  const selectedEntry = useMemo(
    () => entries.find((entry) => entry.id === selectedEntryId) ?? entries[0],
    [entries, selectedEntryId],
  );

  if (catalogQuery.isLoading) {
    return <Spinner />;
  }
  if (catalogQuery.isError) {
    return <ErrorMessage error={catalogQuery.error} onRetry={() => catalogQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <NorthPanel
        title="Media Catalog"
        subtitle="Catalog records for archived content and cartridge placement."
        columns={[
          { key: 'barcode', header: 'Barcode', render: (row: CatalogEntryResponse) => row.barcode },
          { key: 'source_path', header: 'Source Path', render: (row: CatalogEntryResponse) => row.source_path },
          { key: 'size_bytes', header: 'Size', render: (row: CatalogEntryResponse) => formatBytes(row.size_bytes) },
          { key: 'strategy', header: 'Strategy', render: (row: CatalogEntryResponse) => row.strategy },
          { key: 'shards', header: 'Shards', render: (row: CatalogEntryResponse) => row.shards },
          { key: 'created_at', header: 'Created', render: (row: CatalogEntryResponse) => formatDate(row.created_at) },
        ]}
        rows={entries}
        getRowId={(row) => row.id}
        selectedId={selectedEntry?.id}
        onSelect={(row) => setSelectedEntryId(row.id)}
        emptyMessage="No catalog entries are available."
      />

      <InformationPanel
        title={selectedEntry ? `Catalog Entry ${selectedEntry.id}` : 'Catalog Details'}
        subtitle="Full detail for the selected catalog record."
        items={[
          { label: 'Barcode', value: selectedEntry?.barcode ?? '—' },
          { label: 'Source Path', value: selectedEntry?.source_path ?? '—' },
          { label: 'Size', value: selectedEntry ? formatBytes(selectedEntry.size_bytes) : '—' },
          { label: 'Strategy', value: selectedEntry?.strategy ?? '—' },
          { label: 'Shards', value: selectedEntry?.shards ?? '—' },
          { label: 'Checksum', value: selectedEntry?.checksum ?? '—' },
          { label: 'Created', value: selectedEntry ? formatDate(selectedEntry.created_at) : '—' },
        ]}
      />

      <OperationsPanel
        title="Catalog Operations"
        subtitle="Refresh the catalog or move to archive submission."
        actions={[
          { label: 'Refresh', onClick: () => void catalogQuery.refetch(), variant: 'primary' },
          { label: 'Archive', onClick: () => undefined, variant: 'secondary' },
        ]}
      />
    </div>
  );
}
