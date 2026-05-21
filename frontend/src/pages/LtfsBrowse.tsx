import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getLtfsVolumes, listFiles, mountVolume, type LtfsFile, type LtfsVolume, unmountVolume } from '../api/ltfs';
import { getVolumeGroups } from '../api/volumeGroups';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes, formatDate } from '../lib/utils';

function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}`;
}

function getParentPath(path: string): string {
  const normalized = normalizePath(path);
  if (normalized === '/') return '/';
  const parts = normalized.split('/').filter(Boolean);
  parts.pop();
  return parts.length ? `/${parts.join('/')}` : '/';
}

function breadcrumbs(path: string) {
  const normalized = normalizePath(path);
  if (normalized === '/') return [] as Array<{ label: string; path: string }>;
  return normalized.split('/').filter(Boolean).map((segment, index, segments) => ({
    label: segment,
    path: `/${segments.slice(0, index + 1).join('/')}`,
  }));
}

export default function LtfsBrowse() {
  const queryClient = useQueryClient();
  const [selectedBarcode, setSelectedBarcode] = useState<string>();
  const [currentPath, setCurrentPath] = useState('/');
  const [search, setSearch] = useState('');
  const [selectedFile, setSelectedFile] = useState<LtfsFile | null>(null);
  const [actionError, setActionError] = useState<Record<string, string>>({});

  const volumesQuery = useQuery({ queryKey: ['ltfs', 'volumes'], queryFn: getLtfsVolumes, refetchInterval: 60_000 });
  const volumeGroupsQuery = useQuery({ queryKey: ['volume-groups'], queryFn: getVolumeGroups, refetchInterval: 60_000 });

  const selectedVolume = useMemo(
    () => volumesQuery.data?.find((volume) => volume.barcode === selectedBarcode) ?? volumesQuery.data?.[0],
    [selectedBarcode, volumesQuery.data],
  );

  useEffect(() => {
    if (!selectedBarcode && volumesQuery.data?.length) {
      setSelectedBarcode(volumesQuery.data[0].barcode);
    }
  }, [selectedBarcode, volumesQuery.data]);

  useEffect(() => {
    setCurrentPath('/');
    setSearch('');
    setSelectedFile(null);
  }, [selectedBarcode]);

  const filesQuery = useQuery({
    queryKey: ['ltfs', 'files', selectedVolume?.barcode, currentPath],
    queryFn: () => listFiles(selectedVolume!.barcode, currentPath),
    enabled: Boolean(selectedVolume?.barcode && selectedVolume.hasCatalog),
  });

  const mountMutation = useMutation({
    mutationFn: (barcode: string) => mountVolume(barcode),
    onSuccess: async () => {
      setActionError({});
      await queryClient.invalidateQueries({ queryKey: ['ltfs', 'volumes'] });
    },
    onError: (error, barcode) => {
      setActionError((current) => ({ ...current, [barcode]: error instanceof Error ? error.message : 'Unable to mount tape.' }));
    },
  });

  const unmountMutation = useMutation({
    mutationFn: (barcode: string) => unmountVolume(barcode),
    onSuccess: async (_, barcode) => {
      setActionError((current) => {
        const next = { ...current };
        delete next[barcode];
        return next;
      });
      await queryClient.invalidateQueries({ queryKey: ['ltfs', 'volumes'] });
    },
    onError: (error, barcode) => {
      setActionError((current) => ({ ...current, [barcode]: error instanceof Error ? error.message : 'Unable to unmount tape.' }));
    },
  });

  const visibleFiles = useMemo(() => {
    const files = filesQuery.data?.files ?? [];
    const normalizedSearch = search.trim().toLowerCase();
    if (!normalizedSearch) return files;
    return files.filter((file) => file.name.toLowerCase().includes(normalizedSearch));
  }, [filesQuery.data, search]);

  if (volumesQuery.isLoading || volumeGroupsQuery.isLoading) {
    return <Spinner />;
  }
  if (volumesQuery.isError) {
    return <ErrorMessage error={volumesQuery.error} onRetry={() => void volumesQuery.refetch()} />;
  }
  if (volumeGroupsQuery.isError) {
    return <ErrorMessage error={volumeGroupsQuery.error} onRetry={() => void volumeGroupsQuery.refetch()} />;
  }

  const selectedGroups = (volumeGroupsQuery.data ?? []).filter((group) => (group.barcodes ?? []).includes(selectedVolume?.barcode ?? ''));

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Media</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">LTFS browse</h1>
            <p className="mt-2 text-sm text-slate-400">
              Browse catalog-backed LTFS metadata by tape, with emulator-friendly tape discovery from both
              catalog and AML section inventory. Directory listings are catalog only.
            </p>
          </div>
          <Badge variant="blue">{(volumesQuery.data ?? []).length} tape{(volumesQuery.data ?? []).length === 1 ? '' : 's'}</Badge>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
        <div className="space-y-4">
          <Card>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">LTFS tapes</div>
            <div className="mt-4 space-y-3">
              {(volumesQuery.data ?? []).map((volume) => {
                const selected = selectedVolume?.barcode === volume.barcode;
                const isMutating = mountMutation.variables === volume.barcode || unmountMutation.variables === volume.barcode;
                return (
                  <button
                    key={volume.barcode}
                    type="button"
                    onClick={() => setSelectedBarcode(volume.barcode)}
                    className={`w-full rounded-md border p-4 text-left transition ${selected ? 'border-quantum-red bg-quantum-panel' : 'border-quantum-border bg-quantum-sidebar hover:bg-quantum-panel'}`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="font-semibold text-slate-100">{volume.barcode}</div>
                        <div className="mt-1 text-sm text-slate-400">{volume.label}</div>
                      </div>
                      <Badge variant={volume.hasCatalog ? 'green' : 'amber'}>{volume.hasCatalog ? 'Cataloged' : 'No catalog yet'}</Badge>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-400">
                      <span>{(volume.fileCount ?? 0).toLocaleString()} files</span>
                      <span>{(volume.shardCount ?? 0).toLocaleString()} shards</span>
                      <span>{volume.lastModified ? formatDate(volume.lastModified) : 'No archive metadata'}</span>
                    </div>
                    <div className="mt-4 flex flex-wrap items-center gap-2">
                      <Badge variant={volume.mounted ? 'blue' : 'gray'}>{volume.mounted ? 'Mounted' : 'Offline'}</Badge>
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={isMutating}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (volume.mounted) {
                            unmountMutation.mutate(volume.barcode);
                          } else {
                            mountMutation.mutate(volume.barcode);
                          }
                        }}
                      >
                        {isMutating ? 'Working…' : volume.mounted ? 'Unmount' : 'Mount'}
                      </Button>
                    </div>
                    {actionError[volume.barcode] ? <div className="mt-3 text-xs text-red-300">{actionError[volume.barcode]}</div> : null}
                  </button>
                );
              })}
            </div>
          </Card>

          <Card>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Volume groups</div>
            <div className="mt-4 space-y-3">
              {(volumeGroupsQuery.data ?? []).map((group) => {
                const containsSelected = (group.barcodes ?? []).includes(selectedVolume?.barcode ?? '');
                return (
                  <div key={group.id} className={`rounded-md border px-4 py-3 text-sm ${containsSelected ? 'border-quantum-red bg-quantum-panel text-slate-100' : 'border-quantum-border bg-quantum-sidebar text-slate-300'}`}>
                    <div className="font-semibold">{group.name}</div>
                    <div className="mt-1 text-xs text-slate-400">{(group.barcodes ?? []).join(', ') || 'No tapes assigned'}</div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            {!selectedVolume ? (
              <div className="rounded-md border border-dashed border-quantum-border px-4 py-8 text-center text-sm text-slate-400">
                Select an LTFS tape to inspect catalog metadata.
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Selected tape</div>
                    <div className="mt-1 text-lg font-semibold text-slate-100">{selectedVolume.barcode}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {selectedGroups.map((group) => <Badge key={group.id} variant="blue">{group.name}</Badge>)}
                  </div>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4 text-sm text-slate-300">
                  <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Catalog files</div><div className="mt-1 text-slate-100">{(selectedVolume.fileCount ?? 0).toLocaleString()}</div></div>
                  <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Metadata shards</div><div className="mt-1 text-slate-100">{(selectedVolume.shardCount ?? 0).toLocaleString()}</div></div>
                  <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Mount point</div><div className="mt-1 text-slate-100">{selectedVolume.mountPoint ?? 'Not mounted'}</div></div>
                  <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Last catalog update</div><div className="mt-1 text-slate-100">{selectedVolume.lastModified ? formatDate(selectedVolume.lastModified) : '—'}</div></div>
                </div>
              </div>
            )}
          </Card>

          <Card>
            {!selectedVolume ? null : !selectedVolume.hasCatalog ? (
              <div className="rounded-md border border-dashed border-quantum-border px-4 py-8 text-center text-sm text-slate-400">
                {selectedVolume.barcode} is visible through the emulator, but there is no catalog metadata yet.
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap items-center gap-2 text-sm text-slate-300">
                    <button type="button" className="font-semibold text-slate-100" onClick={() => setCurrentPath('/')}>{selectedVolume.barcode}</button>
                    {breadcrumbs(currentPath).map((segment) => (
                      <span key={segment.path} className="flex items-center gap-2">
                        <span className="text-slate-500">/</span>
                        <button type="button" className="hover:text-white" onClick={() => setCurrentPath(segment.path)}>{segment.label}</button>
                      </span>
                    ))}
                    <span className="text-slate-500">/</span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="secondary" disabled={currentPath === '/'} onClick={() => setCurrentPath(getParentPath(currentPath))}>Up</Button>
                    <input
                      value={search}
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="Search folder"
                      className="w-56 rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                    />
                  </div>
                </div>

                {filesQuery.isLoading ? <Spinner /> : filesQuery.isError ? <ErrorMessage error={filesQuery.error} onRetry={() => void filesQuery.refetch()} /> : (
                  <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                    <div className="overflow-hidden rounded-md border border-quantum-border">
                      <table className="min-w-full divide-y divide-quantum-border text-sm">
                        <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                          <tr>
                            <th className="px-4 py-3">Name</th>
                            <th className="px-4 py-3">Size</th>
                            <th className="px-4 py-3">Modified</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-quantum-border/80 bg-quantum-panel">
                          {visibleFiles.length === 0 ? (
                            <tr><td colSpan={3} className="px-4 py-10 text-center text-slate-400">No files match this directory view.</td></tr>
                          ) : visibleFiles.map((file) => (
                            <tr key={file.path} className="text-slate-200 hover:bg-black/10">
                              <td className="px-4 py-3">
                                {file.type === 'directory' ? (
                                  <button type="button" className="flex items-center gap-2 font-medium text-slate-100 hover:text-white" onClick={() => { setCurrentPath(file.path); setSelectedFile(null); }}>
                                    <span>📁</span>{file.name}
                                  </button>
                                ) : (
                                  <button type="button" className="flex items-center gap-2 hover:text-white" onClick={() => setSelectedFile(file)}>
                                    <span>📄</span>{file.name}
                                  </button>
                                )}
                              </td>
                              <td className="px-4 py-3 text-slate-300">{file.type === 'directory' ? '—' : formatBytes(file.size)}</td>
                              <td className="px-4 py-3 text-slate-300">{formatDate(file.modified)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="rounded-md border border-quantum-border bg-quantum-sidebar p-4">
                      <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Metadata shard detail</div>
                      {selectedFile ? (
                        <div className="mt-4 space-y-3 text-sm text-slate-300">
                          <div><div className="text-xs uppercase tracking-[0.16em] text-slate-500">File</div><div className="mt-1 text-slate-100">{selectedFile.name}</div></div>
                          <div><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Path</div><div className="mt-1 break-all">{selectedFile.path}</div></div>
                          <div><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape</div><div className="mt-1">{selectedFile.tapeBarcode ?? selectedVolume.barcode}</div></div>
                          <div><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Shard count</div><div className="mt-1">{selectedFile.shardCount ?? 1}</div></div>
                          <div><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Modified</div><div className="mt-1">{formatDate(selectedFile.modified)}</div></div>
                        </div>
                      ) : <div className="mt-4 text-sm text-slate-400">Select a file to inspect catalog metadata.</div>}
                    </div>
                  </div>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
