import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { getCartridges } from '../api/cartridges';
import { postRestore } from '../api/restore';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes } from '../lib/utils';

export default function Catalog() {
  const cartridgesQuery = useQuery({ queryKey: ['cartridges'], queryFn: getCartridges, refetchInterval: 30_000 });
  const [fileId, setFileId] = useState('');
  const [destinationPath, setDestinationPath] = useState('');
  const restoreMutation = useMutation({
    mutationFn: postRestore,
  });

  if (cartridgesQuery.isLoading) {
    return <Spinner />;
  }
  if (cartridgesQuery.isError) {
    return <ErrorMessage error={cartridgesQuery.error} onRetry={() => cartridgesQuery.refetch()} />;
  }

  const cartridges = cartridgesQuery.data ?? [];

  return (
    <div className="space-y-6">
      <Card className="space-y-4">
        <div>
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Restore</p>
          <h2 className="mt-1 text-xl font-semibold text-white">Restore request</h2>
        </div>
        <form
          className="grid gap-4 lg:grid-cols-[1fr,1fr,auto] lg:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            restoreMutation.mutate({ file_id: fileId, destination_path: destinationPath });
          }}
        >
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-300">Catalog path / file id</label>
            <input value={fileId} onChange={(event) => setFileId(event.target.value)} placeholder="/photos/2025/raw/shot.nef" required />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-300">Destination path</label>
            <input value={destinationPath} onChange={(event) => setDestinationPath(event.target.value)} placeholder="/restore/photos" required />
          </div>
          <Button type="submit" disabled={restoreMutation.isPending}>
            {restoreMutation.isPending ? 'Queueing…' : 'Start restore'}
          </Button>
        </form>
        {restoreMutation.isSuccess ? (
          <p className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
            Restore job queued: {restoreMutation.data.job_id}
          </p>
        ) : null}
        {restoreMutation.isError ? <ErrorMessage error={restoreMutation.error} /> : null}
      </Card>

      <Card>
        <div className="mb-4">
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Media catalog</p>
          <h2 className="mt-1 text-xl font-semibold text-white">Cartridge inventory</h2>
        </div>
        <div className="overflow-hidden rounded-xl border border-slate-800">
          <table className="min-w-full divide-y divide-slate-800 text-sm">
            <thead className="bg-slate-900/70 text-left text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Barcode</th>
                <th className="px-4 py-3 font-medium">Volume group</th>
                <th className="px-4 py-3 font-medium">Usage</th>
                <th className="px-4 py-3 font-medium">State</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {cartridges.map((cartridge) => (
                <tr key={cartridge.barcode}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-200">{cartridge.barcode}</td>
                  <td className="px-4 py-3 text-slate-300">{cartridge.volume_group_id ?? 'Unassigned'}</td>
                  <td className="px-4 py-3 text-slate-400">
                    {formatBytes(cartridge.used_bytes)} / {formatBytes(cartridge.capacity_bytes)}
                  </td>
                  <td className="px-4 py-3 text-slate-300">{cartridge.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
