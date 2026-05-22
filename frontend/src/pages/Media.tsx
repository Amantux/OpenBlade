import { useQuery } from '@tanstack/react-query';
import { Search } from 'lucide-react';
import { useMemo, useState } from 'react';
import { listLibraries } from '../api/libraries';
import { listCartridges, type Cartridge } from '../api/media';
import Card from '../components/ui/Card';
import Badge from '../components/ui/Badge';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { listPools, type MediaPool } from '../lib/mediaPoolStore';
import { useLibraryScope } from '../lib/useLibraryScope';
import { formatDate } from '../lib/utils';

const EMPTY_CARTRIDGES: Cartridge[] = [];

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  const normalized = state.toLowerCase();
  if (normalized.includes('error') || normalized.includes('fault')) {
    return 'red';
  }
  if (normalized.includes('mounted') || normalized.includes('loaded')) {
    return 'blue';
  }
  if (normalized.includes('ready') || normalized.includes('home')) {
    return 'green';
  }
  return 'gray';
}

function typeVariant(type: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (type.startsWith('LTO-9')) {
    return 'green';
  }
  if (type.startsWith('LTO-8')) {
    return 'blue';
  }
  if (type.startsWith('LTO-7')) {
    return 'amber';
  }
  return 'gray';
}

function formatTb(valueGB: number | null | undefined): string {
  if (!valueGB || valueGB <= 0) {
    return '0 TB';
  }

  const valueTB = valueGB / 1000;
  return `${valueTB.toFixed(valueTB >= 100 ? 0 : 1)} TB`;
}

function resolvePool(cartridge: Cartridge, pools: MediaPool[]): MediaPool | null {
  if (!cartridge.poolName) {
    return null;
  }

  return pools.find((pool) => pool.name === cartridge.poolName) ?? null;
}

function CapacityBar({ usedGB, totalGB }: { usedGB: number; totalGB: number }) {
  const percent = totalGB > 0 ? Math.min(100, Math.max(0, (usedGB / totalGB) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-slate-900/80">
        <div className="h-full rounded-full bg-quantum-red" style={{ width: `${percent}%` }} />
      </div>
      <div className="text-xs text-slate-400">{formatTb(usedGB)} / {formatTb(totalGB)}</div>
    </div>
  );
}

export default function Media() {
  const { libraryId, libraryName: activeLibraryName, isAll, setLibrary } = useLibraryScope();
  const mediaQuery = useQuery({ queryKey: ['media', 'cartridges', libraryId], queryFn: listCartridges, refetchInterval: 30_000 });
  const poolsQuery = useQuery({ queryKey: ['media', 'pools', libraryId], queryFn: listPools, refetchInterval: 30_000 });
  const librariesQuery = useQuery({ queryKey: ['libraries'], queryFn: listLibraries, refetchInterval: 30_000 });
  const [search, setSearch] = useState('');
  const [poolFilter, setPoolFilter] = useState('all');
  const [stateFilter, setStateFilter] = useState('all');
  const [selectedBarcode, setSelectedBarcode] = useState<string | null>(null);

  const media = mediaQuery.data ?? EMPTY_CARTRIDGES;
  const pools = poolsQuery.data ?? [];
  const libraries = librariesQuery.data ?? [];
  const states = useMemo(() => Array.from(new Set(media.map((cartridge) => cartridge.state))).sort(), [media]);
  const poolNames = useMemo(
    () => Array.from(new Set(media.map((cartridge) => cartridge.poolName).filter((value): value is string => Boolean(value)).concat(pools.map((pool) => pool.name)))).sort(),
    [media, pools],
  );

  const rows = useMemo(
    () =>
      media.map((cartridge) => ({
        cartridge,
        pool: resolvePool(cartridge, pools),
      })),
    [media, pools],
  );

  const filteredRows = useMemo(
    () =>
      rows.filter(({ cartridge }) => {
        const matchesSearch = cartridge.barcode.toLowerCase().includes(search.toLowerCase());
        const matchesPool =
          poolFilter === 'all'
            ? true
            : poolFilter === 'unassigned'
              ? !cartridge.poolName
              : cartridge.poolName === poolFilter;
        const matchesState = stateFilter === 'all' ? true : cartridge.state === stateFilter;
        return matchesSearch && matchesPool && matchesState;
      }),
    [poolFilter, rows, search, stateFilter],
  );

  const activeSelectedBarcode = filteredRows.some(({ cartridge }) => cartridge.barcode === selectedBarcode)
    ? selectedBarcode
    : filteredRows[0]?.cartridge.barcode ?? null;

  const selected = activeSelectedBarcode
    ? filteredRows.find(({ cartridge }) => cartridge.barcode === activeSelectedBarcode) ?? null
    : null;

  if (mediaQuery.isLoading || poolsQuery.isLoading || librariesQuery.isLoading) {
    return <Spinner />;
  }

  if (mediaQuery.isError) {
    return <ErrorMessage error={mediaQuery.error} onRetry={() => mediaQuery.refetch()} />;
  }

  if (poolsQuery.isError) {
    return <ErrorMessage error={poolsQuery.error} onRetry={() => poolsQuery.refetch()} />;
  }

  if (librariesQuery.isError) {
    return <ErrorMessage error={librariesQuery.error} onRetry={() => librariesQuery.refetch()} />;
  }

  const selectedLibraryName = isAll
    ? 'No library selected'
    : activeLibraryName || (libraries.find((library) => String(library.id) === libraryId)?.name ?? 'Selected Library');

  return (
    <div className="space-y-4">
      <div>
        <div className="text-xs uppercase tracking-[0.28em] text-slate-500">Media</div>
        <h1 className="mt-2 text-3xl font-semibold text-white">Cartridges</h1>
        <p className="mt-2 text-sm text-slate-400">Live AML cartridge inventory with backend pool assignments, slot telemetry, and operational counters.</p>
      </div>

      <div className="rounded-md border border-blue-500/20 bg-blue-500/5 px-4 py-3 text-sm text-slate-300">
        <span className="font-medium text-blue-200">ℹ Demo mode:</span>{' '}
        Cartridge data is simulated. In production, each library serves its own tape inventory.
      </div>

      <Card className="bg-quantum-panel p-5">
        <div className="flex flex-wrap items-center gap-2 pb-4 text-xs text-slate-400">
          <span className="rounded border border-quantum-border bg-quantum-sidebar px-2 py-1">Scope: {selectedLibraryName}</span>
          <button
            type="button"
            onClick={() => setLibrary('', '')}
            className={`rounded-full border px-2.5 py-1 transition ${isAll ? 'border-amber-500/40 bg-amber-500/15 text-amber-300' : 'border-quantum-border bg-quantum-sidebar text-slate-300 hover:text-white'}`}
          >
            No Library
          </button>
          {libraries.map((library) => {
            const isSelected = libraryId === String(library.id);
            return (
              <button
                key={library.id}
                type="button"
                onClick={() => setLibrary(String(library.id), library.name)}
                className={`rounded-full border px-2.5 py-1 transition ${isSelected ? 'border-blue-500/40 bg-blue-500/15 text-blue-300' : 'border-quantum-border bg-quantum-sidebar text-slate-300 hover:text-white'}`}
              >
                {library.name}
              </button>
            );
          })}
        </div>
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr),220px,220px]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search barcode"
              className="w-full rounded-md border border-quantum-border bg-quantum-sidebar py-2 pl-9 pr-3 text-sm text-white"
            />
          </label>
          <select
            value={poolFilter}
            onChange={(event) => setPoolFilter(event.target.value)}
            className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white"
          >
            <option value="all">All Pools</option>
            <option value="unassigned">Unassigned</option>
            {poolNames.map((poolName) => (
              <option key={poolName} value={poolName}>{poolName}</option>
            ))}
          </select>
          <select
            value={stateFilter}
            onChange={(event) => setStateFilter(event.target.value)}
            className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white"
          >
            <option value="all">All States</option>
            {states.map((state) => (
              <option key={state} value={state}>{state}</option>
            ))}
          </select>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr),minmax(320px,1fr)]">
        <Card className="overflow-hidden bg-quantum-panel p-0">
          <div className="flex items-center justify-between border-b border-quantum-border px-5 py-4">
            <div>
              <h2 className="text-lg font-semibold text-white">Tape Inventory</h2>
              <p className="mt-1 text-sm text-slate-400">{filteredRows.length} of {rows.length} cartridges shown</p>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-quantum-border text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-[0.18em] text-slate-500">
                  <th className="px-4 py-3">Barcode</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3">Pool</th>
                  <th className="px-4 py-3">Slot Address</th>
                  <th className="px-4 py-3">Loads</th>
                  <th className="px-4 py-3">Errors</th>
                  <th className="px-4 py-3">Capacity</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-quantum-border/80">
                {filteredRows.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-10 text-center text-slate-400">No cartridges match the current filters.</td>
                  </tr>
                ) : (
                  filteredRows.map(({ cartridge, pool }) => {
                    const active = selectedBarcode === cartridge.barcode;
                    return (
                      <tr
                        key={cartridge.barcode}
                        className={active ? 'bg-quantum-info/80' : 'hover:bg-quantum-info/40'}
                        onClick={() => setSelectedBarcode(cartridge.barcode)}
                      >
                        <td className="cursor-pointer px-4 py-3 font-medium text-white">{cartridge.barcode}</td>
                        <td className="px-4 py-3"><Badge variant={typeVariant(cartridge.type)}>{cartridge.type}</Badge></td>
                        <td className="px-4 py-3"><Badge variant={stateVariant(cartridge.state)}>{cartridge.state}</Badge></td>
                        <td className="px-4 py-3">
                          {cartridge.poolName ? (
                            pool ? (
                              <span
                                className="inline-flex rounded-full border px-2.5 py-1 text-xs font-medium"
                                style={{ borderColor: pool.color, backgroundColor: `${pool.color}22`, color: pool.color }}
                              >
                                {pool.name}
                              </span>
                            ) : (
                              <span className="inline-flex rounded-full border border-slate-600 bg-slate-800 px-2.5 py-1 text-xs font-medium text-slate-200">
                                {cartridge.poolName}
                              </span>
                            )
                          ) : (
                            <span className="text-slate-400">Unassigned</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-slate-300">{cartridge.slotAddress ?? cartridge.location ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-300">{cartridge.loadCount ?? '—'}</td>
                        <td className="px-4 py-3 text-slate-300">{cartridge.errorCount ?? '—'}</td>
                        <td className="min-w-[200px] px-4 py-3">
                          <CapacityBar usedGB={cartridge.usedGB ?? 0} totalGB={cartridge.capacityGB ?? 0} />
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="bg-quantum-panel p-5">
          <div>
            <h2 className="text-lg font-semibold text-white">Cartridge Detail</h2>
            <p className="mt-1 text-sm text-slate-400">Select a row to inspect all mapped AML metadata.</p>
          </div>

          {selected ? (
            <div className="mt-5 space-y-5">
              <div>
                <div className="text-2xl font-semibold text-white">{selected.cartridge.barcode}</div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <Badge variant={typeVariant(selected.cartridge.type)}>{selected.cartridge.type}</Badge>
                  <Badge variant={stateVariant(selected.cartridge.state)}>{selected.cartridge.state}</Badge>
                </div>
              </div>

              <CapacityBar usedGB={selected.cartridge.usedGB ?? 0} totalGB={selected.cartridge.capacityGB ?? 0} />

              <dl className="grid gap-3 text-sm">
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Pool</dt>
                  <dd className="text-right text-white">{selected.cartridge.poolName ?? 'Unassigned'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Slot Address</dt>
                  <dd className="text-right text-white">{selected.cartridge.slotAddress ?? selected.cartridge.location ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Partition</dt>
                  <dd className="text-right text-white">{selected.cartridge.partition ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Usage</dt>
                  <dd className="text-right text-white">{selected.cartridge.percentUsed ?? 0}%</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Write Protected</dt>
                  <dd className="text-right text-white">{selected.cartridge.writeProtected ? 'Yes' : 'No'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">WORM</dt>
                  <dd className="text-right text-white">{selected.cartridge.worm ? 'Yes' : 'No'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Generations</dt>
                  <dd className="text-right text-white">{selected.cartridge.generations ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Load Count</dt>
                  <dd className="text-right text-white">{selected.cartridge.loadCount ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Error Count</dt>
                  <dd className="text-right text-white">{selected.cartridge.errorCount ?? '—'}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-info/60 px-3 py-2">
                  <dt className="text-slate-400">Last Loaded</dt>
                  <dd className="text-right text-white">{selected.cartridge.lastLoaded ? formatDate(selected.cartridge.lastLoaded) : '—'}</dd>
                </div>
              </dl>
            </div>
          ) : (
            <div className="mt-5 rounded-md border border-dashed border-quantum-border px-4 py-10 text-center text-sm text-slate-400">
              No cartridge selected.
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
