import { X } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { rootApiRequest } from '../api/client';
import { formatBytes, formatDate } from '../lib/utils';
import type { CatalogFile, CatalogFileDetail, CatalogListResponse, EnqueuedJobResponse } from '../types/api';

const PAGE_SIZE = 25;

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 px-4 py-6">
      <div className="relative w-full max-w-2xl rounded-xl border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <button
          type="button"
          className="absolute right-4 top-4 rounded-full p-1 text-slate-400 transition hover:bg-quantum-panel hover:text-white"
          onClick={onClose}
          aria-label="Close modal"
        >
          <X className="h-5 w-5" />
        </button>
        {children}
      </div>
    </div>
  );
}

async function getCatalogFiles(search: string, offset: number): Promise<CatalogListResponse> {
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
  if (search) {
    params.set('search', search);
  }
  return rootApiRequest<CatalogListResponse>(`/catalog/?${params.toString()}`);
}

async function getCatalogFileDetail(fileId: string): Promise<CatalogFileDetail> {
  return rootApiRequest<CatalogFileDetail>(`/catalog/${fileId}`);
}

export default function Catalog() {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0);
  const [restoreFile, setRestoreFile] = useState<CatalogFile>();
  const [destinationPath, setDestinationPath] = useState('');
  const [successMessage, setSuccessMessage] = useState<string>();

  const catalogQuery = useQuery({
    queryKey: ['catalog', search, offset],
    queryFn: () => getCatalogFiles(search, offset),
    refetchInterval: 30_000,
  });
  const detailQuery = useQuery({
    queryKey: ['catalog', restoreFile?.id, 'detail'],
    queryFn: () => getCatalogFileDetail(restoreFile!.id),
    enabled: Boolean(restoreFile),
  });
  const restoreMutation = useMutation({
    mutationFn: ({ sourcePath, destPath }: { sourcePath: string; destPath: string }) =>
      rootApiRequest<EnqueuedJobResponse>('/restore/', {
        method: 'POST',
        body: {
          source_path: sourcePath,
          catalog_path: sourcePath,
          dest_path: destPath,
        },
      }),
    onSuccess: (_, variables) => {
      setSuccessMessage(`Restore queued for ${variables.sourcePath}.`);
      setRestoreFile(undefined);
      setDestinationPath('');
    },
  });

  useEffect(() => {
    if (!restoreFile) {
      setDestinationPath('');
    }
  }, [restoreFile]);

  const files = catalogQuery.data?.files ?? [];
  const total = catalogQuery.data?.total ?? 0;
  const canGoBack = offset > 0;
  const canGoForward = offset + PAGE_SIZE < total;
  const rangeLabel = total === 0 ? '0 results' : `${offset + 1}-${Math.min(offset + files.length, total)} of ${total}`;
  const tapeBarcodes = useMemo(
    () => Array.from(new Set((detailQuery.data?.instances ?? []).map((instance) => instance.barcode))),
    [detailQuery.data?.instances],
  );

  if (catalogQuery.isLoading) {
    return <Spinner />;
  }
  if (catalogQuery.isError) {
    return <ErrorMessage error={catalogQuery.error} onRetry={() => catalogQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Catalog</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Archived Files</h1>
            <p className="mt-1 text-sm text-slate-400">Browse archived files and launch restores from the catalog.</p>
          </div>
          <form
            className="flex w-full max-w-xl flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault();
              setOffset(0);
              setSearch(searchInput.trim());
            }}
          >
            <input
              className="flex-1"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              placeholder="Search source paths"
            />
            <div className="flex gap-2">
              <Button type="submit">Search</Button>
              <Button
                type="button"
                variant="secondary"
                onClick={() => {
                  setSearchInput('');
                  setSearch('');
                  setOffset(0);
                }}
              >
                Clear
              </Button>
            </div>
          </form>
        </div>
        {successMessage ? (
          <div className="mt-4 rounded-md border border-emerald-700 bg-emerald-900/20 px-3 py-3 text-sm text-emerald-200">
            {successMessage}
          </div>
        ) : null}
      </Card>

      <Card>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white">Catalog Records</h2>
            <p className="mt-1 text-sm text-slate-400">{rangeLabel}</p>
          </div>
          <div className="flex gap-2">
            <Button variant="secondary" onClick={() => void catalogQuery.refetch()}>
              Refresh
            </Button>
            <Button variant="ghost" disabled={!canGoBack} onClick={() => setOffset((current) => Math.max(0, current - PAGE_SIZE))}>
              Previous
            </Button>
            <Button variant="ghost" disabled={!canGoForward} onClick={() => setOffset((current) => current + PAGE_SIZE)}>
              Next
            </Button>
          </div>
        </div>

        {total === 0 ? (
          <div className="rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
            {search ? 'No matching archives.' : 'No archives yet — run your first archive.'}
          </div>
        ) : (
          <div className="overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Source Path</th>
                  <th className="px-4 py-3 font-medium">Size</th>
                  <th className="px-4 py-3 font-medium">Checksum</th>
                  <th className="px-4 py-3 font-medium">Tapes</th>
                  <th className="px-4 py-3 font-medium">Shards</th>
                  <th className="px-4 py-3 font-medium">Archived At</th>
                  <th className="px-4 py-3 font-medium text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {files.map((file, index) => (
                  <tr key={file.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 text-slate-200">{file.source_path}</td>
                    <td className="px-4 py-3 text-slate-300">{formatBytes(file.size_bytes)}</td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-300">{file.checksum.slice(0, 8)}</td>
                    <td className="px-4 py-3 text-slate-300">{file.instance_count}</td>
                    <td className="px-4 py-3 text-slate-300">{file.shard_count}</td>
                    <td className="px-4 py-3 text-slate-300">{formatDate(file.created_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setSuccessMessage(undefined);
                          setRestoreFile(file);
                        }}
                      >
                        Restore
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {restoreFile ? (
        <Modal
          onClose={() => {
            setRestoreFile(undefined);
            restoreMutation.reset();
          }}
        >
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Restore File</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-100">Restore from Catalog</h2>
            <p className="mt-1 text-sm text-slate-400">Review the archived file details and choose a destination path.</p>
          </div>

          {detailQuery.isLoading ? <div className="mt-6"><Spinner /></div> : null}
          {detailQuery.isError ? <div className="mt-6"><ErrorMessage error={detailQuery.error} onRetry={() => detailQuery.refetch()} /></div> : null}
          {detailQuery.data ? (
            <form
              className="mt-6 grid gap-4"
              onSubmit={(event: FormEvent<HTMLFormElement>) => {
                event.preventDefault();
                restoreMutation.mutate({
                  sourcePath: detailQuery.data.source_path,
                  destPath: destinationPath,
                });
              }}
            >
              <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
                <div className="grid gap-3 md:grid-cols-2">
                  <div>
                    <div className="text-xs uppercase tracking-wide text-slate-500">Source Path</div>
                    <div className="mt-1 break-all text-slate-100">{detailQuery.data.source_path}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-wide text-slate-500">Size</div>
                    <div className="mt-1 text-slate-100">{formatBytes(detailQuery.data.size_bytes)}</div>
                  </div>
                  <div className="md:col-span-2">
                    <div className="text-xs uppercase tracking-wide text-slate-500">Tape Barcodes</div>
                    <div className="mt-1 text-slate-100">{tapeBarcodes.length > 0 ? tapeBarcodes.join(', ') : '—'}</div>
                  </div>
                </div>
              </div>

              <label className="grid gap-2 text-sm text-slate-300">
                <span>Destination Path</span>
                <input
                  className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                  value={destinationPath}
                  onChange={(event) => setDestinationPath(event.target.value)}
                  placeholder="/restore/output"
                  required
                />
              </label>

              {restoreMutation.isError ? <ErrorMessage error={restoreMutation.error} /> : null}

              <div className="flex flex-wrap justify-end gap-2 pt-2">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setRestoreFile(undefined);
                    restoreMutation.reset();
                  }}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={restoreMutation.isPending || !destinationPath.trim()}>
                  {restoreMutation.isPending ? 'Submitting…' : 'Queue Restore'}
                </Button>
              </div>
            </form>
          ) : null}
        </Modal>
      ) : null}
    </div>
  );
}
