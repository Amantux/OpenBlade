import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { listManifestVersions } from '../api/catalogAdmin';
import { formatDate } from '../lib/utils';

function truncateSha(value: string): string {
  return value.length > 14 ? `${value.slice(0, 14)}…` : value;
}

export default function ManifestVersionsPage() {
  const [barcodeFilter, setBarcodeFilter] = useState('');
  const [copiedSha, setCopiedSha] = useState<string | null>(null);
  const manifestQuery = useQuery({
    queryKey: ['manifest-versions'],
    queryFn: () => listManifestVersions(),
  });

  const rows = useMemo(() => {
    const filter = barcodeFilter.trim().toLowerCase();
    return (manifestQuery.data ?? []).filter((record) => record.barcode.toLowerCase().includes(filter));
  }, [barcodeFilter, manifestQuery.data]);

  useEffect(() => {
    if (!copiedSha) {
      return undefined;
    }

    const timer = window.setTimeout(() => {
      setCopiedSha((current) => (current === copiedSha ? null : current));
    }, 1500);

    return () => window.clearTimeout(timer);
  }, [copiedSha]);

  const copySha = async (value: string) => {
    await navigator.clipboard.writeText(value);
    setCopiedSha(value);
  };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Catalog</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Manifest versions</h1>
            <p className="mt-2 text-sm text-slate-400">Browse manifest history across all known tape barcodes.</p>
          </div>
          <label className="w-full max-w-sm text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.18em] text-slate-500">Filter by barcode</span>
            <input
              value={barcodeFilter}
              onChange={(event) => setBarcodeFilter(event.target.value)}
              placeholder="Search barcode"
              className="w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-white"
            />
          </label>
        </div>
      </Card>

      <Card>
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-white">Manifest inventory</h2>
            <p className="mt-1 text-sm text-slate-400">{rows.length} records shown</p>
          </div>
          <Button variant="secondary" onClick={() => void manifestQuery.refetch()}>
            Refresh
          </Button>
        </div>

        {manifestQuery.isLoading ? <Spinner /> : null}
        {manifestQuery.isError ? <ErrorMessage error={manifestQuery.error} onRetry={() => manifestQuery.refetch()} /> : null}
        {!manifestQuery.isLoading && !manifestQuery.isError ? (
          rows.length === 0 ? (
            <div className="rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
              No manifest versions match the current filter.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Barcode</th>
                    <th className="px-4 py-3 font-medium">Version Number</th>
                    <th className="px-4 py-3 font-medium">Written At</th>
                    <th className="px-4 py-3 font-medium">SHA256</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((record, index) => {
                    const status = (record.status ?? 'valid').toLowerCase();
                    return (
                      <tr key={record.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                        <td className="px-4 py-3 font-medium text-slate-100">{record.barcode}</td>
                        <td className="px-4 py-3 text-slate-300">v{record.version_number ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-300">{formatDate(record.written_at ?? '')}</td>
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            <code className="font-mono text-xs text-slate-300">{truncateSha(record.sha256)}</code>
                            <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => void copySha(record.sha256)}>
                              {copiedSha === record.sha256 ? 'Copied' : 'Copy'}
                            </Button>
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant={status === 'corrupt' ? 'red' : 'green'}>{status}</Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}
