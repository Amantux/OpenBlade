import { useQuery } from '@tanstack/react-query';
import { Archive, Database, HardDrive, Package, Pencil, Plus, X, type LucideIcon } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { listCartridges, type Cartridge } from '../api/media';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import {
  MEDIA_POOL_COLOR_PRESETS,
  MEDIA_POOL_GENERATIONS,
  MEDIA_POOL_STORE_EVENT,
  assignCartridge,
  createPool,
  deletePool,
  getPools,
  type MediaPool,
  type PoolPolicy,
  unassignCartridge,
  updatePool,
} from '../lib/mediaPoolStore';
import { cn, formatDate } from '../lib/utils';

const EMPTY_CARTRIDGES: Cartridge[] = [];

interface PoolFormState {
  name: string;
  policy: PoolPolicy;
  maxDrives: number;
  targetLtoGeneration: string;
  quotaGB: string;
  color: string;
}

interface PoolStats {
  cartridges: Cartridge[];
  totalGB: number;
  usedGB: number;
}

const emptyForm: PoolFormState = {
  name: '',
  policy: 'standard',
  maxDrives: 4,
  targetLtoGeneration: '',
  quotaGB: '',
  color: MEDIA_POOL_COLOR_PRESETS[0],
};

function formatTb(valueGB: number | null | undefined): string {
  if (!valueGB || valueGB <= 0) {
    return '0 TB';
  }

  const valueTB = valueGB / 1000;
  return `${valueTB.toFixed(valueTB >= 100 ? 0 : 1)} TB`;
}

function isDataCartridge(cartridge: Cartridge): boolean {
  return (cartridge.capacityGB ?? 0) > 0 && /^LTO-[789]/.test(cartridge.type);
}

function getPolicyBadge(pool: MediaPool): string {
  if (pool.policy === 'critical') {
    return 'Sequential';
  }
  if (pool.policy === 'archive') {
    return 'WORM Archive';
  }
  return `Parallel (${pool.maxDrives})`;
}

function getPolicyCopy(pool: MediaPool): string {
  if (pool.policy === 'critical') {
    return 'Sequential';
  }
  if (pool.policy === 'archive') {
    return `WORM archive • ${pool.maxDrives} drives`;
  }
  return `Parallel • ${pool.maxDrives} drives`;
}

function getPoolStats(pool: MediaPool, cartridges: Cartridge[]): PoolStats {
  const assigned = cartridges.filter((cartridge) => cartridge.poolName === pool.name);
  return {
    cartridges: assigned,
    totalGB: assigned.reduce((sum, cartridge) => sum + (cartridge.capacityGB ?? 0), 0),
    usedGB: assigned.reduce((sum, cartridge) => sum + (cartridge.usedGB ?? 0), 0),
  };
}

function getPoolForm(pool?: MediaPool): PoolFormState {
  if (!pool) {
    return emptyForm;
  }

  return {
    name: pool.name,
    policy: pool.policy,
    maxDrives: pool.maxDrives,
    targetLtoGeneration: pool.targetLtoGeneration ?? '',
    quotaGB: pool.quotaGB ? String(pool.quotaGB) : '',
    color: pool.color,
  };
}

function isCompatible(pool: MediaPool, cartridge: Cartridge): boolean {
  if (!isDataCartridge(cartridge)) {
    return false;
  }

  return pool.targetLtoGeneration === null || cartridge.type === pool.targetLtoGeneration;
}

function Modal({ title, subtitle, children, onClose }: { title: string; subtitle?: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 px-4 py-6">
      <div className="relative max-h-[90vh] w-full max-w-4xl overflow-y-auto rounded-xl border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <button
          type="button"
          className="absolute right-4 top-4 rounded-full p-1 text-slate-400 transition hover:bg-quantum-panel hover:text-white"
          onClick={onClose}
          aria-label="Close modal"
        >
          <X className="h-5 w-5" />
        </button>
        <div className="pr-8">
          <h2 className="text-xl font-semibold text-white">{title}</h2>
          {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
        </div>
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}

function SummaryStat({ label, value, icon: Icon }: { label: string; value: string; icon: LucideIcon }) {
  return (
    <Card className="bg-quantum-panel p-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
          <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
        </div>
        <div className="rounded-full border border-quantum-border bg-quantum-info p-3 text-slate-300">
          <Icon className="h-5 w-5" />
        </div>
      </div>
    </Card>
  );
}

function ProgressBar({ color, percent }: { color: string; percent: number }) {
  return (
    <div className="h-3 overflow-hidden rounded-full bg-slate-900/80">
      <div className="h-full rounded-full transition-all" style={{ width: `${Math.max(0, Math.min(100, percent))}%`, backgroundColor: color }} />
    </div>
  );
}

export default function MediaPools() {
  const mediaQuery = useQuery({ queryKey: ['media', 'cartridges'], queryFn: listCartridges, refetchInterval: 30_000 });
  const [pools, setPools] = useState<MediaPool[]>(() => getPools());
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [editingPool, setEditingPool] = useState<MediaPool | null>(null);
  const [assignPool, setAssignPool] = useState<MediaPool | null>(null);
  const [detailPool, setDetailPool] = useState<MediaPool | null>(null);
  const [form, setForm] = useState<PoolFormState>(emptyForm);
  const [bulkAssignPoolId, setBulkAssignPoolId] = useState('');
  const [selectedUnassigned, setSelectedUnassigned] = useState<string[]>([]);
  const [selectedAssignBarcodes, setSelectedAssignBarcodes] = useState<string[]>([]);

  useEffect(() => {
    const syncPools = () => setPools(getPools());
    window.addEventListener(MEDIA_POOL_STORE_EVENT, syncPools);
    window.addEventListener('storage', syncPools);
    return () => {
      window.removeEventListener(MEDIA_POOL_STORE_EVENT, syncPools);
      window.removeEventListener('storage', syncPools);
    };
  }, []);

  const cartridges = mediaQuery.data ?? EMPTY_CARTRIDGES;

  const summary = useMemo(() => {
    const totalCapacityGB = cartridges.reduce((sum, cartridge) => sum + (cartridge.capacityGB ?? 0), 0);
    const totalUsedGB = cartridges.reduce((sum, cartridge) => sum + (cartridge.usedGB ?? 0), 0);
    return {
      totalCartridges: cartridges.length,
      totalCapacityGB,
      totalUsedGB,
    };
  }, [cartridges]);

  const unassignedCartridges = useMemo(
    () => cartridges.filter((cartridge) => !cartridge.poolName),
    [cartridges],
  );

  const unassignedAssignable = useMemo(
    () => unassignedCartridges.filter((cartridge) => isDataCartridge(cartridge)),
    [unassignedCartridges],
  );

  const eligibleForAssignModal = useMemo(
    () => (assignPool ? unassignedAssignable.filter((cartridge) => isCompatible(assignPool, cartridge)) : []),
    [assignPool, unassignedAssignable],
  );

  const refreshPools = () => setPools(getPools());
  const refreshAll = async () => {
    refreshPools();
    await mediaQuery.refetch();
  };
  const detailPoolStats = detailPool ? getPoolStats(detailPool, cartridges) : null;

  const openCreateModal = () => {
    setEditingPool(null);
    setForm(emptyForm);
    setShowCreateModal(true);
  };

  const openEditModal = (pool: MediaPool) => {
    setEditingPool(pool);
    setForm(getPoolForm(pool));
    setShowCreateModal(true);
  };

  const handleSavePool = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const payload = {
      name: form.name.trim(),
      policy: form.policy,
      maxDrives: form.policy === 'critical' ? 1 : form.maxDrives,
      targetLtoGeneration: form.targetLtoGeneration || null,
      quotaGB: form.quotaGB ? Number(form.quotaGB) : null,
      assignedBarcodes: editingPool ? getPoolStats(editingPool, cartridges).cartridges.map((cartridge) => cartridge.barcode) : [],
      color: form.color,
    };

    if (!payload.name) {
      return;
    }

    try {
      if (editingPool) {
        await updatePool(editingPool.id, payload);
      } else {
        await createPool(payload);
      }

      await refreshAll();
      setShowCreateModal(false);
      setEditingPool(null);
      setForm(emptyForm);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Failed to save media pool.');
    }
  };

  const handleDeletePool = async () => {
    if (!editingPool) {
      return;
    }

    if (window.confirm(`Delete pool “${editingPool.name}”? Assigned cartridges will become unassigned.`)) {
      try {
        await deletePool(editingPool.id);
        await refreshAll();
        setShowCreateModal(false);
        setEditingPool(null);
        setForm(emptyForm);
      } catch (error) {
        window.alert(error instanceof Error ? error.message : 'Failed to delete media pool.');
      }
    }
  };

  const toggleUnassigned = (barcode: string) => {
    setSelectedUnassigned((current) =>
      current.includes(barcode) ? current.filter((item) => item !== barcode) : [...current, barcode],
    );
  };

  const toggleAssignSelection = (barcode: string) => {
    setSelectedAssignBarcodes((current) =>
      current.includes(barcode) ? current.filter((item) => item !== barcode) : [...current, barcode],
    );
  };

  const handleBulkAssign = async () => {
    if (!bulkAssignPoolId || selectedUnassigned.length === 0) {
      return;
    }

    try {
      await Promise.all(selectedUnassigned.map((barcode) => assignCartridge(bulkAssignPoolId, barcode)));
      await refreshAll();
      setSelectedUnassigned([]);
      setBulkAssignPoolId('');
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Failed to assign cartridges.');
    }
  };

  const handleAssignToPool = async () => {
    if (!assignPool || selectedAssignBarcodes.length === 0) {
      return;
    }

    try {
      await Promise.all(selectedAssignBarcodes.map((barcode) => assignCartridge(assignPool.id, barcode)));
      await refreshAll();
      setSelectedAssignBarcodes([]);
      setAssignPool(null);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : 'Failed to assign cartridges.');
    }
  };

  if (mediaQuery.isLoading) {
    return <Spinner />;
  }

  if (mediaQuery.isError) {
    return <ErrorMessage error={mediaQuery.error} onRetry={() => mediaQuery.refetch()} />;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.28em] text-slate-500">Media</div>
          <h1 className="mt-2 text-3xl font-semibold text-white">Media Pools</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-400">
            NAS-style tape capacity management with sequential critical pools, parallel archive lanes, and per-pool LTO targeting.
          </p>
        </div>
        <Button className="gap-2 self-start" onClick={openCreateModal}>
          <Plus className="h-4 w-4" />
          New Pool
        </Button>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <SummaryStat label="Total Pools" value={String(pools.length)} icon={Database} />
        <SummaryStat label="Total Cartridges" value={String(summary.totalCartridges)} icon={Package} />
        <SummaryStat label="Total Capacity" value={formatTb(summary.totalCapacityGB)} icon={HardDrive} />
        <SummaryStat label="Used" value={formatTb(summary.totalUsedGB)} icon={Archive} />
      </div>

      <div className="grid gap-4 xl:grid-cols-3">
        {pools.map((pool) => {
          const stats = getPoolStats(pool, cartridges);
          const percentUsed = stats.totalGB > 0 ? (stats.usedGB / stats.totalGB) * 100 : 0;
          const overQuotaGB = pool.quotaGB !== null ? Math.max(0, stats.usedGB - pool.quotaGB) : 0;

          return (
            <Card key={pool.id} className="border-l-4 bg-quantum-panel p-0" style={{ borderLeftColor: pool.color }}>
              <div className="space-y-4 p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-3">
                      <span className="inline-block h-3 w-3 rounded-full" style={{ backgroundColor: pool.color }} />
                      <h2 className="text-lg font-semibold text-white">{pool.name}</h2>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span
                        className="inline-flex items-center gap-1 rounded-full border border-transparent px-2.5 py-1 text-xs font-medium tracking-wide"
                        style={{ backgroundColor: `${pool.color}22`, color: pool.color }}
                      >
                        {getPolicyBadge(pool)}
                      </span>
                    </div>
                  </div>
                  <Button variant="ghost" className="gap-2 px-3 py-1.5 text-xs" onClick={() => openEditModal(pool)}>
                    <Pencil className="h-4 w-4" />
                    Edit
                  </Button>
                </div>

                <div className="space-y-1 text-sm text-slate-400">
                  <div>Policy: {getPolicyCopy(pool)}</div>
                  <div>
                    {pool.targetLtoGeneration ?? 'Any LTO'} • {stats.cartridges.length} cartridges
                  </div>
                </div>

                <div className="space-y-2">
                  <ProgressBar color={pool.color} percent={percentUsed} />
                  <div className="flex items-center justify-between text-sm text-slate-300">
                    <span>{Math.round(percentUsed)}% used</span>
                    <span>{formatTb(stats.usedGB)} / {formatTb(stats.totalGB)}</span>
                  </div>
                </div>

                <div className="space-y-1 text-sm text-slate-400">
                  <div>Quota: {pool.quotaGB === null ? 'Unlimited' : formatTb(pool.quotaGB)}</div>
                  {overQuotaGB > 0 ? <div className="text-amber-300">Over quota by {formatTb(overQuotaGB)}</div> : null}
                  <div>Created: {formatDate(pool.createdAt)}</div>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button variant="secondary" className="px-3 py-1.5 text-xs" onClick={() => {
                    setAssignPool(pool);
                    setSelectedAssignBarcodes([]);
                  }}>
                    Assign Media
                  </Button>
                  <Button variant="ghost" className="px-3 py-1.5 text-xs" onClick={() => setDetailPool(pool)}>
                    View Cartridges
                  </Button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <Card className="bg-quantum-panel p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white">Unassigned cartridges</h2>
            <p className="mt-1 text-sm text-slate-400">Select unpooled media and assign it into a NAS-style tape capacity pool.</p>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <select
              value={bulkAssignPoolId}
              onChange={(event) => setBulkAssignPoolId(event.target.value)}
              className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100"
            >
              <option value="">Assign to Pool</option>
              {pools.map((pool) => (
                <option key={pool.id} value={pool.id}>{pool.name}</option>
              ))}
            </select>
            <Button variant="secondary" disabled={!bulkAssignPoolId || selectedUnassigned.length === 0} onClick={handleBulkAssign}>
              Assign Selected
            </Button>
          </div>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                <th className="px-3 py-3">
                  <input
                    type="checkbox"
                    className="rounded border border-quantum-border bg-quantum-sidebar"
                    checked={unassignedAssignable.length > 0 && selectedUnassigned.length === unassignedAssignable.length}
                    onChange={(event) =>
                      setSelectedUnassigned(event.target.checked ? unassignedAssignable.map((cartridge) => cartridge.barcode) : [])
                    }
                  />
                </th>
                <th className="px-3 py-3">Barcode</th>
                <th className="px-3 py-3">Type</th>
                <th className="px-3 py-3">State</th>
                <th className="px-3 py-3">Slot Address</th>
                <th className="px-3 py-3">Capacity</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {unassignedCartridges.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-3 py-8 text-center text-slate-400">All cartridges are currently assigned to a pool.</td>
                </tr>
              ) : (
                unassignedCartridges.map((cartridge) => {
                  const assignable = isDataCartridge(cartridge);
                  return (
                    <tr key={cartridge.barcode} className="text-slate-200">
                      <td className="px-3 py-3 align-middle">
                        <input
                          type="checkbox"
                          disabled={!assignable}
                          checked={selectedUnassigned.includes(cartridge.barcode)}
                          onChange={() => toggleUnassigned(cartridge.barcode)}
                          className="rounded border border-quantum-border bg-quantum-sidebar disabled:opacity-40"
                        />
                      </td>
                      <td className="px-3 py-3 font-medium text-white">{cartridge.barcode}</td>
                      <td className="px-3 py-3">{cartridge.type}</td>
                      <td className="px-3 py-3">{cartridge.state}</td>
                      <td className="px-3 py-3">{cartridge.slotAddress ?? cartridge.location ?? '—'}</td>
                      <td className="px-3 py-3 text-slate-300">{assignable ? formatTb(cartridge.capacityGB) : 'N/A'}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {showCreateModal ? (
        <Modal
          title={editingPool ? `Edit ${editingPool.name}` : 'Create Media Pool'}
          subtitle="Define policy, drive parallelism, preferred LTO generation, quota, and display color."
          onClose={() => {
            setShowCreateModal(false);
            setEditingPool(null);
            setForm(emptyForm);
          }}
        >
          <form className="space-y-6" onSubmit={handleSavePool}>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="space-y-2 text-sm text-slate-300">
                <span className="block">Name</span>
                <input
                  value={form.name}
                  onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                  className="w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-white"
                  placeholder="Pool name"
                  required
                />
              </label>
              <label className="space-y-2 text-sm text-slate-300">
                <span className="block">LTO Generation</span>
                <select
                  value={form.targetLtoGeneration}
                  onChange={(event) => setForm((current) => ({ ...current, targetLtoGeneration: event.target.value }))}
                  className="w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-white"
                >
                  <option value="">Any</option>
                  {MEDIA_POOL_GENERATIONS.map((generation) => (
                    <option key={generation} value={generation}>{generation}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="space-y-3">
              <div className="text-sm font-medium text-slate-300">Policy</div>
              <div className="grid gap-3 md:grid-cols-3">
                {([
                  { id: 'critical', label: 'Critical', copy: 'Sequential, dedicated single drive' },
                  { id: 'standard', label: 'Standard', copy: 'Parallel writes across multiple drives' },
                  { id: 'archive', label: 'Archive', copy: 'WORM-style closed archive pool' },
                ] as const).map((option) => (
                  <label key={option.id} className={cn(
                    'cursor-pointer rounded-lg border px-4 py-3 text-sm transition',
                    form.policy === option.id ? 'border-quantum-red bg-quantum-red/10 text-white' : 'border-quantum-border bg-quantum-panel text-slate-300',
                  )}>
                    <input
                      type="radio"
                      name="policy"
                      value={option.id}
                      checked={form.policy === option.id}
                      onChange={() => setForm((current) => ({ ...current, policy: option.id }))}
                      className="sr-only"
                    />
                    <div className="font-semibold">{option.label}</div>
                    <div className="mt-1 text-xs text-slate-400">{option.copy}</div>
                  </label>
                ))}
              </div>
            </div>

            {form.policy !== 'critical' ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-sm text-slate-300">
                  <span>Max Drives</span>
                  <span className="font-semibold text-white">{form.maxDrives}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={8}
                  value={form.maxDrives}
                  onChange={(event) => setForm((current) => ({ ...current, maxDrives: Number(event.target.value) }))}
                  className="w-full accent-quantum-red"
                />
              </div>
            ) : null}

            <div className="grid gap-4 md:grid-cols-2">
              <label className="space-y-2 text-sm text-slate-300">
                <span className="block">Quota (GB)</span>
                <input
                  type="number"
                  min={1}
                  value={form.quotaGB}
                  onChange={(event) => setForm((current) => ({ ...current, quotaGB: event.target.value }))}
                  className="w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-white"
                  placeholder="Unlimited"
                />
              </label>
              <div className="space-y-2 text-sm text-slate-300">
                <span className="block">Color</span>
                <div className="flex flex-wrap gap-3">
                  {MEDIA_POOL_COLOR_PRESETS.map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setForm((current) => ({ ...current, color }))}
                      className={cn(
                        'h-10 w-10 rounded-full border-2 transition',
                        form.color === color ? 'border-white scale-110' : 'border-transparent',
                      )}
                      style={{ backgroundColor: color }}
                      aria-label={`Select ${color}`}
                    />
                  ))}
                </div>
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <div>
                {editingPool ? (
                  <Button type="button" variant="danger" onClick={handleDeletePool}>
                    Delete Pool
                  </Button>
                ) : null}
              </div>
              <div className="flex gap-3">
                <Button type="button" variant="ghost" onClick={() => {
                  setShowCreateModal(false);
                  setEditingPool(null);
                  setForm(emptyForm);
                }}>
                  Cancel
                </Button>
                <Button type="submit">{editingPool ? 'Save Changes' : 'Create Pool'}</Button>
              </div>
            </div>
          </form>
        </Modal>
      ) : null}

      {assignPool ? (
        <Modal
          title={`Assign Media • ${assignPool.name}`}
          subtitle={`Showing unassigned cartridges compatible with ${assignPool.targetLtoGeneration ?? 'any LTO generation'}.`}
          onClose={() => {
            setAssignPool(null);
            setSelectedAssignBarcodes([]);
          }}
        >
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="text-sm text-slate-400">Eligible cartridges: {eligibleForAssignModal.length}</div>
              <Button
                type="button"
                variant="ghost"
                onClick={() =>
                  setSelectedAssignBarcodes(
                    selectedAssignBarcodes.length === eligibleForAssignModal.length ? [] : eligibleForAssignModal.map((cartridge) => cartridge.barcode),
                  )
                }
              >
                {selectedAssignBarcodes.length === eligibleForAssignModal.length ? 'Clear Selection' : 'Select All'}
              </Button>
            </div>

            <div className="overflow-x-auto rounded-lg border border-quantum-border">
              <table className="min-w-full divide-y divide-quantum-border text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                    <th className="px-3 py-3">Select</th>
                    <th className="px-3 py-3">Barcode</th>
                    <th className="px-3 py-3">Type</th>
                    <th className="px-3 py-3">State</th>
                    <th className="px-3 py-3">Slot Address</th>
                    <th className="px-3 py-3">Capacity</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-quantum-border/80">
                  {eligibleForAssignModal.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-3 py-8 text-center text-slate-400">No compatible unassigned cartridges are available.</td>
                    </tr>
                  ) : (
                    eligibleForAssignModal.map((cartridge) => (
                      <tr key={cartridge.barcode} className="text-slate-200">
                        <td className="px-3 py-3">
                          <input
                            type="checkbox"
                            checked={selectedAssignBarcodes.includes(cartridge.barcode)}
                            onChange={() => toggleAssignSelection(cartridge.barcode)}
                            className="rounded border border-quantum-border bg-quantum-sidebar"
                          />
                        </td>
                        <td className="px-3 py-3 font-medium text-white">{cartridge.barcode}</td>
                        <td className="px-3 py-3">{cartridge.type}</td>
                        <td className="px-3 py-3">{cartridge.state}</td>
                        <td className="px-3 py-3">{cartridge.slotAddress ?? cartridge.location ?? '—'}</td>
                        <td className="px-3 py-3">{formatTb(cartridge.capacityGB)}</td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            <div className="flex justify-end gap-3">
              <Button type="button" variant="ghost" onClick={() => {
                setAssignPool(null);
                setSelectedAssignBarcodes([]);
              }}>
                Cancel
              </Button>
              <Button type="button" disabled={selectedAssignBarcodes.length === 0} onClick={handleAssignToPool}>
                Assign Selected
              </Button>
            </div>
          </div>
        </Modal>
      ) : null}

      {detailPool ? (
        <Modal
          title={detailPool.name}
          subtitle={`${getPolicyBadge(detailPool)} • ${detailPool.targetLtoGeneration ?? 'Any LTO'} • ${detailPoolStats?.cartridges.length ?? 0} assigned`}
          onClose={() => setDetailPool(null)}
        >
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
              <span>Created {formatDate(detailPool.createdAt)}</span>
              <span>Quota: {detailPool.quotaGB === null ? 'Unlimited' : formatTb(detailPool.quotaGB)}</span>
            </div>
            <div className="overflow-x-auto rounded-lg border border-quantum-border">
              <table className="min-w-full divide-y divide-quantum-border text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                    <th className="px-3 py-3">Barcode</th>
                    <th className="px-3 py-3">Type</th>
                    <th className="px-3 py-3">State</th>
                    <th className="px-3 py-3">Used</th>
                    <th className="px-3 py-3">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-quantum-border/80">
                  {detailPoolStats?.cartridges.length === 0 ? (
                    <tr>
                      <td colSpan={5} className="px-3 py-8 text-center text-slate-400">No cartridges are assigned to this pool yet.</td>
                    </tr>
                  ) : (
                    detailPoolStats?.cartridges.map((cartridge) => (
                      <tr key={cartridge.barcode} className="text-slate-200">
                        <td className="px-3 py-3 font-medium text-white">{cartridge.barcode}</td>
                        <td className="px-3 py-3">{cartridge.type}</td>
                        <td className="px-3 py-3">{cartridge.state}</td>
                        <td className="px-3 py-3">{formatTb(cartridge.usedGB)} / {formatTb(cartridge.capacityGB)}</td>
                        <td className="px-3 py-3">
                          <Button
                            type="button"
                            variant="ghost"
                            className="px-2 py-1 text-xs"
                            onClick={async () => {
                              try {
                                await unassignCartridge(detailPool.id, cartridge.barcode);
                                await refreshAll();
                              } catch (error) {
                                window.alert(error instanceof Error ? error.message : 'Failed to unassign cartridge.');
                              }
                            }}
                          >
                            Unassign
                          </Button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
