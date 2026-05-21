import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { cancelHydrationJob, listHydrationJobs, listVirtualDirectory, requestHydration, statVirtualPath, type HydrationJob, type VirtualFileEntry, type VirtualFileStatus } from '../api/virtualFs';
import { cn, formatBytes, formatDate, formatDuration, toTitleCase, type BadgeVariant } from '../lib/utils';

function normalizePath(path: string): string {
  if (!path.trim()) {
    return '/';
  }
  const normalized = path.replace(/\/+/g, '/');
  return normalized.startsWith('/') ? normalized : `/${normalized}`;
}

function inferPool(path: string, fallback?: string): string {
  if (fallback) {
    return fallback;
  }
  const parts = normalizePath(path).split('/').filter(Boolean);
  if (parts[0] === 'pools' && parts[1]) {
    return parts[1];
  }
  return '';
}

function fileStatusVariant(status: VirtualFileStatus): BadgeVariant {
  switch (status) {
    case 'online_cached':
      return 'green';
    case 'offline_on_tape':
      return 'amber';
    case 'hydrating':
      return 'blue';
    case 'missing_tape':
    case 'failed':
    case 'corrupt':
      return 'red';
    case 'exported':
    default:
      return 'gray';
  }
}

function jobStatusVariant(status: HydrationJob['status']): BadgeVariant {
  switch (status) {
    case 'completed':
      return 'green';
    case 'queued':
    case 'running':
      return 'blue';
    case 'failed':
      return 'red';
    case 'cancelled':
    default:
      return 'gray';
  }
}

function breadcrumbs(path: string): Array<{ label: string; path: string }> {
  const parts = normalizePath(path).split('/').filter(Boolean);
  return [
    { label: '/', path: '/' },
    ...parts.map((part, index) => ({
      label: part,
      path: `/${parts.slice(0, index + 1).join('/')}`,
    })),
  ];
}

function progress(job: HydrationJob): string {
  if (job.total_files <= 0) {
    return '0/0';
  }
  return `${job.completed_files}/${job.total_files}`;
}

function jobDuration(job: HydrationJob): string {
  const start = new Date(job.created_at).getTime();
  const end = new Date(job.updated_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) {
    return '—';
  }
  return formatDuration(Math.round((end - start) / 1000));
}

export default function VirtualFileBrowserPage() {
  const queryClient = useQueryClient();
  const [currentPath, setCurrentPath] = useState('/');
  const [selectedFilePath, setSelectedFilePath] = useState<string>('');

  const listingQuery = useQuery({
    queryKey: ['virtual', 'listing', currentPath],
    queryFn: () => listVirtualDirectory(currentPath),
  });
  const jobsQuery = useQuery({
    queryKey: ['virtual', 'jobs'],
    queryFn: listHydrationJobs,
    refetchInterval: 5_000,
  });
  const fileDetailsQuery = useQuery({
    queryKey: ['virtual', 'stat', selectedFilePath],
    queryFn: () => statVirtualPath(selectedFilePath),
    enabled: Boolean(selectedFilePath),
  });

  const hydrateMutation = useMutation({
    mutationFn: (entry: VirtualFileEntry) =>
      requestHydration({
        paths: [entry.path],
        pool: inferPool(entry.path, entry.pool),
      }),
    onSuccess: async (_, entry) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['virtual', 'listing', currentPath] }),
        queryClient.invalidateQueries({ queryKey: ['virtual', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual', 'stat', entry.path] }),
      ]);
      setSelectedFilePath(entry.path);
    },
  });

  const cancelMutation = useMutation({
    mutationFn: (jobId: string) => cancelHydrationJob(jobId),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['virtual', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual', 'listing'] }),
        queryClient.invalidateQueries({ queryKey: ['virtual', 'stat'] }),
      ]);
    },
  });

  const entries = useMemo(
    () =>
      [...(listingQuery.data?.entries ?? [])].sort((left, right) => {
        if (left.is_directory !== right.is_directory) {
          return left.is_directory ? -1 : 1;
        }
        return left.name.localeCompare(right.name);
      }),
    [listingQuery.data?.entries],
  );

  const activeJobs = useMemo(
    () => (jobsQuery.data ?? []).filter((job) => job.status === 'queued' || job.status === 'running'),
    [jobsQuery.data],
  );

  const queryError = listingQuery.error ?? jobsQuery.error ?? fileDetailsQuery.error;

  function navigateTo(path: string) {
    setCurrentPath(normalizePath(path));
    setSelectedFilePath('');
  }

  if (listingQuery.isLoading && !listingQuery.data) {
    return <Spinner />;
  }

  if (queryError && !listingQuery.data && !jobsQuery.data) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void listingQuery.refetch();
      void jobsQuery.refetch();
      void fileDetailsQuery.refetch();
    }} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Files</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Virtual File Browser</h1>
            <p className="mt-2 text-sm text-slate-400">Browse logical datasets, request restores for offline files, and monitor the hydration queue.</p>
          </div>
          <Button variant="secondary" onClick={() => {
            void listingQuery.refetch();
            void jobsQuery.refetch();
            void fileDetailsQuery.refetch();
          }}>
            Refresh
          </Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.4fr,0.8fr]">
        <div className="space-y-4">
          <Card>
            <div className="flex flex-wrap items-center gap-2 text-sm text-slate-300">
              {breadcrumbs(currentPath).map((crumb, index) => (
                <div key={crumb.path} className="flex items-center gap-2">
                  <button
                    type="button"
                    className={cn('rounded-md px-2 py-1 transition hover:bg-quantum-sidebar hover:text-white', crumb.path === currentPath && 'bg-quantum-sidebar text-white')}
                    onClick={() => navigateTo(crumb.path)}
                  >
                    {crumb.label}
                  </button>
                  {index < breadcrumbs(currentPath).length - 1 ? <span className="text-slate-500">&gt;</span> : null}
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <div className="mb-4 flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-white">Directory Listing</h2>
                <p className="mt-1 text-sm text-slate-400">{listingQuery.data?.total_entries ?? 0} item(s) under {currentPath}</p>
              </div>
              {listingQuery.isFetching ? <div className="text-sm text-slate-400">Refreshing…</div> : null}
            </div>

            {listingQuery.isError ? <ErrorMessage error={listingQuery.error} onRetry={() => listingQuery.refetch()} /> : null}

            {entries.length === 0 ? (
              <div className="rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
                No files or directories found at this path.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-md border border-quantum-border">
                <table className="min-w-full text-sm">
                  <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                    <tr>
                      <th className="px-4 py-3 font-medium">Name</th>
                      <th className="px-4 py-3 font-medium">Size</th>
                      <th className="px-4 py-3 font-medium">Status</th>
                      <th className="px-4 py-3 font-medium">Tape Barcode</th>
                      <th className="px-4 py-3 font-medium">Last Modified</th>
                      <th className="px-4 py-3 font-medium text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((entry, index) => {
                      const isSelected = selectedFilePath === entry.path;
                      return (
                        <tr key={entry.path} className={cn(index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel', isSelected && 'ring-1 ring-inset ring-quantum-red')}>
                          <td className="px-4 py-3">
                            <button
                              type="button"
                              className="text-left text-slate-100 transition hover:text-white"
                              onClick={() => {
                                if (entry.is_directory) {
                                  navigateTo(entry.path);
                                  return;
                                }
                                setSelectedFilePath(entry.path);
                              }}
                            >
                              <div className="font-medium">{entry.name}</div>
                              <div className="mt-1 text-xs text-slate-500">{entry.path}</div>
                            </button>
                          </td>
                          <td className="px-4 py-3 text-slate-300">{entry.is_directory ? '—' : formatBytes(entry.size_bytes)}</td>
                          <td className="px-4 py-3">
                            <Badge variant={fileStatusVariant(entry.status)} className={entry.status === 'hydrating' ? 'animate-pulse' : undefined}>
                              {toTitleCase(entry.status)}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 font-mono text-xs text-slate-300">{entry.tape_barcode || '—'}</td>
                          <td className="px-4 py-3 text-slate-300">{formatDate(entry.mtime)}</td>
                          <td className="px-4 py-3 text-right">
                            {!entry.is_directory && entry.status === 'offline_on_tape' ? (
                              <Button
                                variant="secondary"
                                disabled={hydrateMutation.isPending}
                                onClick={() => hydrateMutation.mutate(entry)}
                              >
                                {hydrateMutation.isPending && hydrateMutation.variables?.path === entry.path ? 'Queueing…' : 'Request Restore'}
                              </Button>
                            ) : entry.is_directory ? (
                              <Button variant="ghost" onClick={() => navigateTo(entry.path)}>
                                Open
                              </Button>
                            ) : (
                              <Button variant="ghost" onClick={() => setSelectedFilePath(entry.path)}>
                                Details
                              </Button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>

        <div className="space-y-4">
          <Card>
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Hydration Queue</div>
                <h2 className="mt-1 text-lg font-semibold text-white">Active restores</h2>
              </div>
              <Badge variant="blue">{activeJobs.length}</Badge>
            </div>

            {jobsQuery.isError ? <div className="mt-4 text-sm text-red-300">Unable to load hydration jobs.</div> : null}
            {jobsQuery.isLoading && !jobsQuery.data ? <div className="mt-4 text-sm text-slate-400">Loading queue…</div> : null}
            {(jobsQuery.data ?? []).length === 0 ? (
              <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
                No hydration jobs queued.
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                {(jobsQuery.data ?? []).map((job) => (
                  <div key={job.job_id} className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate font-semibold text-white">{job.paths[0] ?? job.job_id}</div>
                        <div className="mt-1 text-xs text-slate-500">{job.job_id}</div>
                      </div>
                      <Badge variant={jobStatusVariant(job.status)} className={job.status === 'running' ? 'animate-pulse' : undefined}>
                        {toTitleCase(job.status)}
                      </Badge>
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-slate-300">
                      <div className="flex items-center justify-between gap-3"><span>Progress</span><span>{progress(job)}</span></div>
                      <div className="flex items-center justify-between gap-3"><span>Required Tapes</span><span>{job.required_tapes.join(', ') || '—'}</span></div>
                      <div className="flex items-center justify-between gap-3"><span>Updated</span><span>{formatDate(job.updated_at)}</span></div>
                      <div className="flex items-center justify-between gap-3"><span>Duration</span><span>{jobDuration(job)}</span></div>
                    </div>
                    {job.error ? <div className="mt-3 rounded-md border border-red-500/30 bg-red-950/30 px-3 py-2 text-xs text-red-200">{job.error}</div> : null}
                    {!['completed', 'cancelled', 'failed'].includes(job.status) ? (
                      <div className="mt-3 flex justify-end">
                        <Button
                          variant="danger"
                          disabled={cancelMutation.isPending}
                          onClick={() => cancelMutation.mutate(job.job_id)}
                        >
                          {cancelMutation.isPending && cancelMutation.variables === job.job_id ? 'Cancelling…' : 'Cancel Job'}
                        </Button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Card>

          <Card>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">File Details</div>
            <h2 className="mt-1 text-lg font-semibold text-white">Selected file</h2>
            {!selectedFilePath ? (
              <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
                Select a file to load /virtual/stat metadata.
              </div>
            ) : fileDetailsQuery.isLoading ? (
              <div className="mt-4 text-sm text-slate-400">Loading metadata…</div>
            ) : fileDetailsQuery.isError ? (
              <div className="mt-4 text-sm text-red-300">Unable to load file metadata.</div>
            ) : fileDetailsQuery.data ? (
              <div className="mt-4 space-y-3 text-sm text-slate-300">
                <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Path</div>
                  <div className="mt-1 break-all font-mono text-xs text-white">{fileDetailsQuery.data.path}</div>
                </div>
                <div className="grid gap-3 md:grid-cols-2">
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Size</div>
                    <div className="mt-1 text-white">{formatBytes(fileDetailsQuery.data.size_bytes)}</div>
                  </div>
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Last Modified</div>
                    <div className="mt-1 text-white">{formatDate(fileDetailsQuery.data.mtime)}</div>
                  </div>
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Status</div>
                    <div className="mt-1"><Badge variant={fileStatusVariant(fileDetailsQuery.data.status)} className={fileDetailsQuery.data.status === 'hydrating' ? 'animate-pulse' : undefined}>{toTitleCase(fileDetailsQuery.data.status)}</Badge></div>
                  </div>
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape Barcode</div>
                    <div className="mt-1 text-white">{fileDetailsQuery.data.tape_barcode || '—'}</div>
                  </div>
                </div>
              </div>
            ) : null}
          </Card>
        </div>
      </div>
    </div>
  );
}
