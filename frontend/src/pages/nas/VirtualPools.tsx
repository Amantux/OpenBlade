import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  browsePool,
  createPool,
  deletePool,
  listDatasets,
  listPolicies,
  listPools,
  updatePool,
  type HydrationBehavior,
  type NasPool,
  type NasPoolInput,
  type PoolAccessMode,
} from '../../api/nas';
import BytesDisplay from '../../components/nas/BytesDisplay';
import ConfirmDialog from '../../components/nas/ConfirmDialog';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import RestoreRequestModal from '../../components/nas/RestoreRequestModal';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { formatDate, toTitleCase } from '../../lib/utils';

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

interface PoolFormState {
  name: string;
  volume_groups: string;
  default_policy_id: string;
  mount_path: string;
  virtual_mount_enabled: boolean;
  hydration_behavior: HydrationBehavior;
  cache_target: string;
  restore_target: string;
  access_mode: PoolAccessMode;
}

const hydrationOptions: HydrationBehavior[] = ['queue', 'auto', 'manual'];
const accessModes: PoolAccessMode[] = ['read_only', 'read_write'];
const emptyPoolForm: PoolFormState = {
  name: '',
  volume_groups: '',
  default_policy_id: '',
  mount_path: '/openblade/pools/new-pool',
  virtual_mount_enabled: true,
  hydration_behavior: 'queue',
  cache_target: '',
  restore_target: '/openblade/restore',
  access_mode: 'read_only',
};

function poolMountPath(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `/openblade/pools/${slug || 'new-pool'}`;
}

function parseVolumeGroups(value: string): string[] {
  return value
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function PoolModal({
  open,
  form,
  title,
  isSaving,
  policyOptions,
  onChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  form: PoolFormState;
  title: string;
  isSaving: boolean;
  policyOptions: Array<{ id: string; name: string }>;
  onChange: (updater: (current: PoolFormState) => PoolFormState) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 py-8">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Storage</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">{title}</h2>
          </div>
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>
            Close
          </Button>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <input
              value={form.name}
              onChange={(event) =>
                onChange((current) => {
                  const nextName = event.target.value;
                  const currentDerived = poolMountPath(current.name);
                  return {
                    ...current,
                    name: nextName,
                    mount_path: current.mount_path === currentDerived || !current.mount_path ? poolMountPath(nextName) : current.mount_path,
                  };
                })
              }
            />
          </Field>
          <Field label="Mount path">
            <input value={form.mount_path} onChange={(event) => onChange((current) => ({ ...current, mount_path: event.target.value }))} />
          </Field>
          <Field label="Volume groups">
            <input value={form.volume_groups} onChange={(event) => onChange((current) => ({ ...current, volume_groups: event.target.value }))} placeholder="vg-a, vg-b" />
          </Field>
          <Field label="Default policy">
            <select value={form.default_policy_id} onChange={(event) => onChange((current) => ({ ...current, default_policy_id: event.target.value }))}>
              <option value="">None</option>
              {policyOptions.map((policy) => (
                <option key={policy.id} value={policy.id}>{policy.name}</option>
              ))}
            </select>
          </Field>
          <Field label="Hydration behavior">
            <select value={form.hydration_behavior} onChange={(event) => onChange((current) => ({ ...current, hydration_behavior: event.target.value as HydrationBehavior }))}>
              {hydrationOptions.map((value) => (
                <option key={value} value={value}>{toTitleCase(value)}</option>
              ))}
            </select>
          </Field>
          <Field label="Access mode">
            <select value={form.access_mode} onChange={(event) => onChange((current) => ({ ...current, access_mode: event.target.value as PoolAccessMode }))}>
              {accessModes.map((value) => (
                <option key={value} value={value}>{toTitleCase(value)}</option>
              ))}
            </select>
          </Field>
          <Field label="Cache target">
            <input value={form.cache_target} onChange={(event) => onChange((current) => ({ ...current, cache_target: event.target.value }))} placeholder="cache-drive-1" />
          </Field>
          <Field label="Restore target">
            <input value={form.restore_target} onChange={(event) => onChange((current) => ({ ...current, restore_target: event.target.value }))} />
          </Field>
          <label className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200 md:col-span-2">
            <span>Virtual mount enabled</span>
            <input type="checkbox" checked={form.virtual_mount_enabled} onChange={(event) => onChange((current) => ({ ...current, virtual_mount_enabled: event.target.checked }))} />
          </label>
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={isSaving} onClick={onSubmit}>
            {isSaving ? 'Saving…' : 'Save Pool'}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function VirtualPools() {
  const queryClient = useQueryClient();
  const poolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listPools });
  const policiesQuery = useQuery({ queryKey: ['nas', 'policies'], queryFn: listPolicies });
  const datasetsQuery = useQuery({ queryKey: ['nas', 'datasets', 'pool-summary'], queryFn: () => listDatasets() });

  const [selectedPoolId, setSelectedPoolId] = useState<string>('');
  const [currentPath, setCurrentPath] = useState('');
  const [editingPool, setEditingPool] = useState<NasPool | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [poolToDelete, setPoolToDelete] = useState<NasPool | null>(null);
  const [restorePaths, setRestorePaths] = useState<string[] | null>(null);
  const [form, setForm] = useState<PoolFormState>(emptyPoolForm);
  const [toast, setToast] = useState<ToastState | null>(null);

  const browserQuery = useQuery({
    queryKey: ['nas', 'pool-browse', selectedPoolId, currentPath],
    queryFn: () => browsePool(selectedPoolId, currentPath),
    enabled: Boolean(selectedPoolId),
  });

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    const pools = poolsQuery.data ?? [];
    if (!pools.length) {
      setSelectedPoolId('');
      return;
    }
    if (!selectedPoolId || pools.every((pool) => pool.pool_id !== selectedPoolId)) {
      setSelectedPoolId(pools[0].pool_id);
      setCurrentPath('');
    }
  }, [poolsQuery.data, selectedPoolId]);

  const selectedPool = useMemo(
    () => (poolsQuery.data ?? []).find((pool) => pool.pool_id === selectedPoolId) ?? null,
    [poolsQuery.data, selectedPoolId],
  );

  const policyMap = useMemo(
    () => new Map((policiesQuery.data ?? []).map((policy) => [policy.id, policy.name])),
    [policiesQuery.data],
  );

  const fileCountsByPool = useMemo(() => {
    return (datasetsQuery.data ?? []).reduce<Record<string, number>>((acc, dataset) => {
      if (dataset.pool_id) {
        acc[dataset.pool_id] = (acc[dataset.pool_id] ?? 0) + dataset.file_count;
      }
      return acc;
    }, {});
  }, [datasetsQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (payload: NasPoolInput) => (editingPool ? updatePool(editingPool.pool_id, payload) : createPool(payload)),
    onSuccess: async (pool) => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'pools'] });
      setSelectedPoolId(pool.pool_id);
      setCurrentPath('');
      setToast({ type: 'success', message: editingPool ? 'Pool updated.' : 'Pool created.' });
      setIsModalOpen(false);
      setEditingPool(null);
      setForm(emptyPoolForm);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to save pool.' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (poolId: string) => deletePool(poolId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'pools'] });
      setToast({ type: 'success', message: 'Pool deleted.' });
      setPoolToDelete(null);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to delete pool.' });
    },
  });

  const queryError = poolsQuery.error ?? policiesQuery.error ?? datasetsQuery.error ?? browserQuery.error;

  function openCreatePool() {
    setEditingPool(null);
    setForm(emptyPoolForm);
    setIsModalOpen(true);
  }

  function openEditPool(pool: NasPool) {
    setEditingPool(pool);
    setForm({
      name: pool.name,
      volume_groups: pool.volume_groups.join(', '),
      default_policy_id: pool.default_policy_id ?? '',
      mount_path: pool.mount_path,
      virtual_mount_enabled: pool.virtual_mount_enabled,
      hydration_behavior: pool.hydration_behavior,
      cache_target: pool.cache_target ?? '',
      restore_target: pool.restore_target,
      access_mode: pool.access_mode as PoolAccessMode,
    });
    setIsModalOpen(true);
  }

  function submitPool() {
    if (!form.name.trim()) {
      setToast({ type: 'error', message: 'Pool name is required.' });
      return;
    }
    if (!form.mount_path.trim()) {
      setToast({ type: 'error', message: 'Mount path is required.' });
      return;
    }
    saveMutation.mutate({
      pool_id: editingPool?.pool_id,
      name: form.name,
      volume_groups: parseVolumeGroups(form.volume_groups),
      default_policy_id: form.default_policy_id || null,
      mount_path: form.mount_path,
      virtual_mount_enabled: form.virtual_mount_enabled,
      hydration_behavior: form.hydration_behavior,
      cache_target: form.cache_target || null,
      restore_target: form.restore_target,
      access_mode: form.access_mode,
    });
  }

  function navigateTo(logicalPath: string) {
    setCurrentPath(logicalPath);
  }

  if (poolsQuery.isLoading || policiesQuery.isLoading || datasetsQuery.isLoading) {
    return <Spinner />;
  }

  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void poolsQuery.refetch();
      void policiesQuery.refetch();
      void datasetsQuery.refetch();
      void browserQuery.refetch();
    }} />;
  }

  const pools = poolsQuery.data ?? [];
  const browser = browserQuery.data;
  const breadcrumbs = browser?.path ? browser.path.split('/').filter(Boolean) : [];

  return (
    <div className="space-y-4">
      {toast ? (
        <div className={`fixed right-4 top-4 z-50 rounded-md border px-4 py-3 text-sm shadow-lg ${toast.type === 'success' ? 'border-emerald-500/30 bg-emerald-900/90 text-emerald-100' : 'border-red-500/30 bg-red-950/90 text-red-100'}`}>
          {toast.message}
        </div>
      ) : null}

      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Virtual Pools</h1>
            <p className="mt-2 text-sm text-slate-400">Provision virtual mount points, browse pool contents, and queue restores for offline files.</p>
          </div>
          <Button type="button" onClick={openCreatePool}>Create Pool</Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_1.4fr]">
        <Card>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-quantum-border text-sm">
              <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                <tr>
                  <th className="px-3 py-3">Pool Name</th>
                  <th className="px-3 py-3">Mount Path</th>
                  <th className="px-3 py-3">Volume Groups</th>
                  <th className="px-3 py-3">Policy</th>
                  <th className="px-3 py-3">Access Mode</th>
                  <th className="px-3 py-3">Files</th>
                  <th className="px-3 py-3">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-quantum-border/80">
                {pools.map((pool) => (
                  <tr
                    key={pool.pool_id}
                    className={`cursor-pointer text-slate-200 ${selectedPoolId === pool.pool_id ? 'bg-quantum-sidebar/60' : ''}`}
                    onClick={() => {
                      setSelectedPoolId(pool.pool_id);
                      setCurrentPath('');
                    }}
                  >
                    <td className="px-3 py-3">
                      <div className="font-medium text-slate-100">{pool.name}</div>
                      <div className="mt-1 text-xs text-slate-500">{pool.pool_id}</div>
                    </td>
                    <td className="px-3 py-3 font-mono text-xs">{pool.mount_path}</td>
                    <td className="px-3 py-3">{pool.volume_groups.length > 0 ? pool.volume_groups.join(', ') : '—'}</td>
                    <td className="px-3 py-3">{pool.default_policy_id ? policyMap.get(pool.default_policy_id) ?? pool.default_policy_id : '—'}</td>
                    <td className="px-3 py-3"><NasStatusBadge value={pool.access_mode} /></td>
                    <td className="px-3 py-3">{fileCountsByPool[pool.pool_id] ?? 0}</td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
                        <Button type="button" variant="secondary" onClick={() => openEditPool(pool)}>Edit</Button>
                        <Button type="button" variant="danger" onClick={() => setPoolToDelete(pool)}>Delete</Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {pools.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-400">No pools configured yet.</div> : null}
        </Card>

        <Card>
          {selectedPool ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Pool browser</div>
                  <h2 className="mt-1 text-xl font-semibold text-slate-100">{selectedPool.name}</h2>
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-slate-400">
                    <button type="button" className="hover:text-white" onClick={() => navigateTo('')}>/</button>
                    {breadcrumbs.map((segment, index) => {
                      const nextPath = breadcrumbs.slice(0, index + 1).join('/');
                      return (
                        <span key={nextPath} className="flex items-center gap-2">
                          <span>/</span>
                          <button type="button" className="hover:text-white" onClick={() => navigateTo(nextPath)}>{segment}</button>
                        </span>
                      );
                    })}
                  </div>
                </div>
                <Button type="button" variant="secondary" onClick={() => setToast({ type: 'success', message: 'Index rebuild queued.' })}>
                  Rebuild Index
                </Button>
              </div>

              <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Total files</div>
                  <div className="mt-2 text-sm text-slate-100">{browser?.total_files ?? 0}</div>
                </div>
                <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Total bytes</div>
                  <div className="mt-2"><BytesDisplay value={browser?.total_bytes ?? 0} /></div>
                </div>
                <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Online / Offline</div>
                  <div className="mt-2 text-sm text-slate-100">{browser?.online_count ?? 0} / {browser?.offline_count ?? 0}</div>
                </div>
                <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Hydrating</div>
                  <div className="mt-2 text-sm text-slate-100">{browser?.hydrating_count ?? 0}</div>
                </div>
              </div>

              {browserQuery.isLoading ? <Spinner /> : null}
              {browser ? (
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-quantum-border text-sm">
                    <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                      <tr>
                        <th className="px-3 py-3">Icon</th>
                        <th className="px-3 py-3">Name</th>
                        <th className="px-3 py-3">Size</th>
                        <th className="px-3 py-3">Modified</th>
                        <th className="px-3 py-3">State</th>
                        <th className="px-3 py-3">Tape Barcode</th>
                        <th className="px-3 py-3">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-quantum-border/80">
                      {browser.entries.map((entry) => (
                        <tr key={`${entry.type}-${entry.logical_path}`} className="text-slate-200">
                          <td className="px-3 py-3 text-lg">{entry.type === 'directory' ? '📁' : '📄'}</td>
                          <td className="px-3 py-3">
                            {entry.type === 'directory' ? (
                              <button type="button" className="font-medium text-slate-100 hover:text-white" onClick={() => navigateTo(entry.logical_path)}>
                                {entry.name}
                              </button>
                            ) : (
                              <div className="font-medium text-slate-100">{entry.name}</div>
                            )}
                            <div className="mt-1 text-xs text-slate-500">{entry.logical_path}</div>
                          </td>
                          <td className="px-3 py-3">{entry.type === 'file' ? <BytesDisplay value={entry.size_bytes} /> : '—'}</td>
                          <td className="px-3 py-3">{entry.mtime ? formatDate(entry.mtime) : '—'}</td>
                          <td className="px-3 py-3">{entry.state ? <NasStatusBadge value={entry.state} /> : '—'}</td>
                          <td className="px-3 py-3 font-mono text-xs">{entry.tape_barcode ?? '—'}</td>
                          <td className="px-3 py-3">
                            {entry.type === 'file' && entry.state === 'offline_on_tape' ? (
                              <Button type="button" variant="secondary" onClick={() => setRestorePaths([entry.logical_path])}>Request Restore</Button>
                            ) : (
                              '—'
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {browser.entries.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-400">No files in this path.</div> : null}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="px-4 py-12 text-center text-sm text-slate-400">Select a pool to browse its contents.</div>
          )}
        </Card>
      </div>

      <PoolModal
        open={isModalOpen}
        form={form}
        title={editingPool ? 'Edit Virtual Pool' : 'Create Virtual Pool'}
        isSaving={saveMutation.isPending}
        policyOptions={(policiesQuery.data ?? []).map((policy) => ({ id: policy.id, name: policy.name }))}
        onChange={(updater) => setForm((current) => updater(current))}
        onClose={() => {
          setIsModalOpen(false);
          setEditingPool(null);
          setForm(emptyPoolForm);
        }}
        onSubmit={submitPool}
      />

      <ConfirmDialog
        open={Boolean(poolToDelete)}
        title="Delete virtual pool"
        message={poolToDelete ? `Delete ${poolToDelete.name}? This cannot be undone.` : ''}
        confirmLabel="Delete"
        isProcessing={deleteMutation.isPending}
        onCancel={() => setPoolToDelete(null)}
        onConfirm={() => {
          if (poolToDelete) {
            deleteMutation.mutate(poolToDelete.pool_id);
          }
        }}
      />

      {selectedPool && restorePaths ? (
        <RestoreRequestModal
          poolId={selectedPool.pool_id}
          selectedPaths={restorePaths}
          onClose={() => setRestorePaths(null)}
          onEnqueued={() => {
            setRestorePaths(null);
            setToast({ type: 'success', message: 'Restore job enqueued.' });
            void queryClient.invalidateQueries({ queryKey: ['nas', 'restore-jobs'] });
          }}
        />
      ) : null}
    </div>
  );
}
