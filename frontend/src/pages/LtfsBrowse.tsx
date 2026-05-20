import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { getLtfsVolumes, listFiles, mountVolume, type LtfsFile, type LtfsVolume, unmountVolume } from '../api/ltfs';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes, formatDate } from '../lib/utils';

function formatCapacity(valueGB: number): string {
  if (valueGB >= 1024) {
    return `${(valueGB / 1024).toFixed(1)} TB`;
  }

  return `${valueGB.toLocaleString()} GB`;
}

function percentUsed(volume: LtfsVolume): number {
  if (!volume.capacityGB) {
    return 0;
  }

  return Math.min(Math.round((volume.usedGB / volume.capacityGB) * 100), 100);
}

function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}`;
}

function getParentPath(path: string): string {
  const normalized = normalizePath(path);
  if (normalized === '/') {
    return '/';
  }

  const segments = normalized.split('/').filter(Boolean);
  segments.pop();
  return segments.length ? `/${segments.join('/')}` : '/';
}

function breadcrumbSegments(path: string) {
  const normalized = normalizePath(path);
  if (normalized === '/') {
    return [] as Array<{ label: string; path: string }>;
  }

  return normalized
    .split('/')
    .filter(Boolean)
    .map((segment, index, segments) => ({
      label: segment,
      path: `/${segments.slice(0, index + 1).join('/')}`,
    }));
}

function estimateTapePosition(file: LtfsFile): string {
  const seed = `${file.path}${file.size}`.split('').reduce((total, char) => total + char.charCodeAt(0), 0);
  return `${15 + (seed % 78)}% into cartridge`;
}

export default function LtfsBrowse() {
  const queryClient = useQueryClient();
  const [selectedBarcode, setSelectedBarcode] = useState<string>();
  const [currentPath, setCurrentPath] = useState('/');
  const [search, setSearch] = useState('');
  const [selectedFile, setSelectedFile] = useState<LtfsFile | null>(null);
  const [actionError, setActionError] = useState<Record<string, string>>({});

  const volumesQuery = useQuery({ queryKey: ['ltfs', 'volumes'], queryFn: getLtfsVolumes, refetchInterval: 60_000 });

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
    if (selectedVolume && selectedVolume.barcode !== selectedBarcode) {
      setSelectedBarcode(selectedVolume.barcode);
    }
  }, [selectedBarcode, selectedVolume]);

  const filesQuery = useQuery({
    queryKey: ['ltfs', 'files', selectedVolume?.barcode, currentPath],
    queryFn: () => listFiles(selectedVolume!.barcode, currentPath),
    enabled: Boolean(selectedVolume?.barcode && selectedVolume.mounted),
  });

  useEffect(() => {
    setCurrentPath('/');
    setSearch('');
    setSelectedFile(null);
  }, [selectedBarcode]);

  const mountMutation = useMutation({
    mutationFn: (barcode: string) => mountVolume(barcode),
    onSuccess: async () => {
      setActionError({});
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ltfs', 'volumes'] }),
        queryClient.invalidateQueries({ queryKey: ['ltfs', 'files'] }),
      ]);
    },
    onError: (error, barcode) => {
      setActionError((current) => ({
        ...current,
        [barcode]: error instanceof Error ? error.message : 'Failed to mount volume.',
      }));
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
      setSelectedFile(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['ltfs', 'volumes'] }),
        queryClient.invalidateQueries({ queryKey: ['ltfs', 'files'] }),
      ]);
    },
    onError: (error, barcode) => {
      setActionError((current) => ({
        ...current,
        [barcode]: error instanceof Error ? error.message : 'Failed to unmount volume.',
      }));
    },
  });

  const visibleFiles = useMemo(() => {
    const files = filesQuery.data ?? [];
    const normalizedSearch = search.trim().toLowerCase();
    if (!normalizedSearch) {
      return files;
    }

    return files.filter((file) => file.name.toLowerCase().includes(normalizedSearch));
  }, [filesQuery.data, search]);

  if (volumesQuery.isLoading) {
    return <Spinner />;
  }

  if (volumesQuery.isError) {
    return <ErrorMessage error={volumesQuery.error} onRetry={() => void volumesQuery.refetch()} />;
  }

  const volumes = volumesQuery.data ?? [];
  const chartData = volumes.map((volume) => ({
    name: volume.barcode,
    used: Number((volume.usedGB / 1024).toFixed(2)),
    free: Number(((volume.capacityGB - volume.usedGB) / 1024).toFixed(2)),
  }));

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Media</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">LTFS File Browser</h1>
            <p className="mt-2 text-sm text-slate-400">
              Browse LTFS cartridges mounted through AML section endpoints and explore a simulated tape file tree at /media/ltfs.
            </p>
          </div>
          <Badge variant="blue">{volumes.length} volume{volumes.length === 1 ? '' : 's'}</Badge>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-1">
          <Card>
            <div className="space-y-3">
              <div className="text-xs uppercase tracking-[0.22em] text-slate-500">LTFS Volumes</div>
              {volumes.length === 0 ? (
                <div className="rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">
                  No LTFS cartridges are currently cataloged.
                </div>
              ) : (
                volumes.map((volume) => {
                  const selected = selectedVolume?.barcode === volume.barcode;
                  const isMutating = mountMutation.isPending
                    ? mountMutation.variables === volume.barcode
                    : unmountMutation.isPending && unmountMutation.variables === volume.barcode;
                  const percent = percentUsed(volume);

                  return (
                    <button
                      key={volume.barcode}
                      type="button"
                      onClick={() => setSelectedBarcode(volume.barcode)}
                      className={`w-full rounded-md border p-4 text-left transition ${
                        selected ? 'border-quantum-red bg-quantum-panel' : 'border-quantum-border bg-quantum-sidebar hover:bg-quantum-panel'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <div className="text-lg font-semibold text-slate-100">💾 {volume.barcode}</div>
                          <div className="mt-1 text-sm text-slate-400">{volume.label}</div>
                        </div>
                        <Badge variant={volume.mounted ? 'green' : 'gray'}>{volume.mounted ? 'Mounted' : 'Offline'}</Badge>
                      </div>

                      <div className="mt-3 text-sm text-slate-300">
                        {volume.mounted ? `● Mounted at ${volume.mountPoint}` : '○ Not mounted'}
                      </div>

                      <div className="mt-3">
                        <div className="h-2 overflow-hidden rounded-full bg-slate-900">
                          <div className="h-full bg-quantum-red" style={{ width: `${percent}%` }} />
                        </div>
                        <div className="mt-2 text-sm text-slate-300">
                          {percent}% ({formatCapacity(volume.usedGB)} / {formatCapacity(volume.capacityGB)})
                        </div>
                      </div>

                      <div className="mt-3 text-sm text-slate-400">
                        {(volume.fileCount ?? 0).toLocaleString()} files · Last: {volume.lastModified ? formatDate(volume.lastModified) : '—'}
                      </div>

                      {actionError[volume.barcode] ? <div className="mt-3 text-xs text-red-300">{actionError[volume.barcode]}</div> : null}

                      <div className="mt-4">
                        {volume.mounted ? (
                          <Button
                            type="button"
                            variant="secondary"
                            disabled={isMutating}
                            onClick={(event) => {
                              event.stopPropagation();
                              unmountMutation.mutate(volume.barcode);
                            }}
                          >
                            {isMutating ? 'Unmounting…' : 'Unmount'}
                          </Button>
                        ) : (
                          <Button
                            type="button"
                            disabled={isMutating}
                            onClick={(event) => {
                              event.stopPropagation();
                              mountMutation.mutate(volume.barcode);
                            }}
                          >
                            {isMutating ? 'Mounting…' : 'Mount'}
                          </Button>
                        )}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </Card>

          <Card>
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Capacity Overview</div>
            <div className="mt-4 h-72">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 8, right: 8, left: -16, bottom: 8 }}>
                  <CartesianGrid stroke="#1f2937" strokeDasharray="3 3" />
                  <XAxis dataKey="name" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                  <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} unit=" TB" />
                  <Tooltip
                    contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #334155', color: '#e2e8f0' }}
                    formatter={(value, name) => {
                      const numericValue = typeof value === 'number' ? value : Number(value ?? 0);
                      return [`${numericValue.toFixed(2)} TB`, name === 'used' ? 'Used' : 'Free'];
                    }}
                  />
                  <Legend />
                  <Bar dataKey="used" fill="#ef4444" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="free" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </Card>
        </div>

        <Card className="xl:col-span-2">
          {!selectedVolume ? (
            <div className="rounded-md border border-dashed border-quantum-border px-4 py-10 text-center text-sm text-slate-400">
              Select an LTFS volume to browse its contents.
            </div>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Browser</div>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-300">
                    <button type="button" className="font-semibold text-slate-100" onClick={() => setCurrentPath('/')}>{selectedVolume.barcode}</button>
                    {breadcrumbSegments(currentPath).map((segment) => (
                      <span key={segment.path} className="flex items-center gap-2">
                        <span className="text-slate-500">/</span>
                        <button type="button" className="hover:text-white" onClick={() => setCurrentPath(segment.path)}>{segment.label}</button>
                      </span>
                    ))}
                    <span className="text-slate-500">/</span>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <Button variant="secondary" disabled={currentPath === '/'} onClick={() => setCurrentPath(getParentPath(currentPath))}>Up</Button>
                  <input
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                    placeholder="Search current folder"
                    className="w-56 rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none ring-0 placeholder:text-slate-500 focus:border-quantum-red"
                  />
                </div>
              </div>

              {!selectedVolume.mounted ? (
                <div className="rounded-md border border-dashed border-quantum-border px-4 py-10 text-center text-sm text-slate-400">
                  Mount {selectedVolume.barcode} to browse its LTFS directory tree.
                </div>
              ) : filesQuery.isLoading ? (
                <Spinner />
              ) : filesQuery.isError ? (
                <ErrorMessage error={filesQuery.error} onRetry={() => void filesQuery.refetch()} />
              ) : (
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
                          <tr>
                            <td colSpan={3} className="px-4 py-10 text-center text-slate-400">
                              No files match this directory view.
                            </td>
                          </tr>
                        ) : (
                          visibleFiles.map((file) => (
                            <tr key={file.path} className="text-slate-200 hover:bg-black/10">
                              <td className="px-4 py-3">
                                {file.type === 'directory' ? (
                                  <button
                                    type="button"
                                    className="flex items-center gap-2 font-medium text-slate-100 hover:text-white"
                                    onClick={() => {
                                      setCurrentPath(file.path);
                                      setSelectedFile(null);
                                    }}
                                  >
                                    <span>📁</span>
                                    {file.name}
                                  </button>
                                ) : (
                                  <button type="button" className="flex items-center gap-2 hover:text-white" onClick={() => setSelectedFile(file)}>
                                    <span>📄</span>
                                    {file.name}
                                  </button>
                                )}
                              </td>
                              <td className="px-4 py-3 text-slate-300">{file.type === 'directory' ? '—' : formatBytes(file.size)}</td>
                              <td className="px-4 py-3 text-slate-300">{formatDate(file.modified)}</td>
                            </tr>
                          ))
                        )}
                      </tbody>
                    </table>
                  </div>

                  <div className="rounded-md border border-quantum-border bg-quantum-sidebar p-4">
                    <div className="text-xs uppercase tracking-[0.18em] text-slate-500">File Details</div>
                    {selectedFile ? (
                      <div className="mt-4 space-y-3 text-sm text-slate-300">
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Name</div>
                          <div className="mt-1 text-slate-100">{selectedFile.name}</div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Path</div>
                          <div className="mt-1 break-all">{selectedFile.path}</div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Size</div>
                          <div className="mt-1">{formatBytes(selectedFile.size)}</div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Modified</div>
                          <div className="mt-1">{formatDate(selectedFile.modified)}</div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape Position Estimate</div>
                          <div className="mt-1">{estimateTapePosition(selectedFile)}</div>
                        </div>
                      </div>
                    ) : (
                      <div className="mt-4 text-sm text-slate-400">Select a file to inspect LTFS metadata and tape position estimates.</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
