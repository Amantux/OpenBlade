import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowUpDown, Pencil, Plus, RefreshCw, Search, Trash2 } from 'lucide-react';
import {
  createLibrary,
  deleteLibrary,
  listLibraries,
  updateLibrary,
  type LibraryPayload,
  type LibrarySummary,
} from '../api/libraries';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import {
  getActiveLibraryId,
  setActiveLibraryId,
  subscribeActiveLibrary,
} from '../lib/activeLibrary';
import { type BadgeVariant, cn, formatDate, toTitleCase } from '../lib/utils';

interface LibraryFormValues {
  name: string;
  emulator_url: string;
  serial_number: string;
  model: string;
  role: string;
  sort_order: number;
  enabled: boolean;
}

interface LibraryFormModalProps {
  mode: 'create' | 'edit';
  initialValues: LibraryFormValues;
  onClose: () => void;
  onSubmit: (values: LibraryFormValues) => void;
  isPending: boolean;
}

type LibrarySort = 'status' | 'name' | 'sort_order';

const USER_CLEARED_LIBRARY_STORAGE_KEY = 'openblade_user_cleared_library';

function roleVariant(role: string): BadgeVariant {
  switch (role) {
    case 'primary':
      return 'blue';
    case 'archive':
      return 'purple';
    case 'cold_storage':
      return 'gray';
    default:
      return 'amber';
  }
}

function statusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'online':
      return 'green';
    case 'error':
      return 'amber';
    default:
      return 'red';
  }
}

function statusPriority(library: LibrarySummary): number {
  if (!library.enabled) {
    return 4;
  }
  if (library.status === 'online') {
    return 1;
  }
  if (library.status === 'error') {
    return 2;
  }
  return 3;
}

function compareLibraries(left: LibrarySummary, right: LibrarySummary, sortBy: LibrarySort, activeLibraryId: string): number {
  const leftActive = String(left.id) === activeLibraryId;
  const rightActive = String(right.id) === activeLibraryId;
  if (leftActive !== rightActive) {
    return leftActive ? -1 : 1;
  }

  if (sortBy === 'name') {
    return left.name.localeCompare(right.name) || left.sort_order - right.sort_order || left.id - right.id;
  }
  if (sortBy === 'sort_order') {
    return left.sort_order - right.sort_order || left.name.localeCompare(right.name) || left.id - right.id;
  }

  return statusPriority(left) - statusPriority(right)
    || left.sort_order - right.sort_order
    || left.name.localeCompare(right.name)
    || left.id - right.id;
}

function emptyForm(sortOrder: number): LibraryFormValues {
  return {
    name: '',
    emulator_url: 'http://localhost:8010',
    serial_number: '',
    model: 'Scalar i3',
    role: 'primary',
    sort_order: sortOrder,
    enabled: true,
  };
}

function toFormValues(library: LibrarySummary): LibraryFormValues {
  return {
    name: library.name,
    emulator_url: library.emulator_url,
    serial_number: library.serial_number ?? '',
    model: library.model,
    role: library.role,
    sort_order: library.sort_order,
    enabled: library.enabled,
  };
}

function toPayload(values: LibraryFormValues): LibraryPayload {
  return {
    name: values.name.trim(),
    emulator_url: values.emulator_url.trim(),
    serial_number: values.serial_number.trim() || null,
    model: values.model.trim() || 'Scalar i3',
    role: values.role,
    sort_order: Number(values.sort_order) || 0,
    enabled: values.enabled,
  };
}

function LibraryFormModal({ mode, initialValues, onClose, onSubmit, isPending }: LibraryFormModalProps) {
  const [values, setValues] = useState<LibraryFormValues>(initialValues);

  useEffect(() => {
    setValues(initialValues);
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 py-6">
      <div className="w-full max-w-2xl rounded-md border border-quantum-border bg-quantum-panel p-5 shadow-2xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{mode === 'create' ? 'Add Library' : 'Edit Library'}</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-100">
              {mode === 'create' ? 'Provision library endpoint' : 'Update library settings'}
            </h2>
          </div>
          <Button type="button" variant="ghost" onClick={onClose}>Close</Button>
        </div>

        <div className="mt-5 grid gap-4 md:grid-cols-2">
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Name</span>
            <input
              value={values.name}
              onChange={(event) => setValues((current) => ({ ...current, name: event.target.value }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Emulator URL</span>
            <input
              value={values.emulator_url}
              onChange={(event) => setValues((current) => ({ ...current, emulator_url: event.target.value }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Serial Number</span>
            <input
              value={values.serial_number}
              onChange={(event) => setValues((current) => ({ ...current, serial_number: event.target.value }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Model</span>
            <input
              value={values.model}
              onChange={(event) => setValues((current) => ({ ...current, model: event.target.value }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </label>
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Role</span>
            <select
              value={values.role}
              onChange={(event) => setValues((current) => ({ ...current, role: event.target.value }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            >
              <option value="primary">Primary</option>
              <option value="archive">Archive</option>
              <option value="cold_storage">Cold Storage</option>
            </select>
          </label>
          <label className="block text-sm text-slate-300">
            <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Sort Order</span>
            <input
              type="number"
              value={values.sort_order}
              onChange={(event) => setValues((current) => ({ ...current, sort_order: Number(event.target.value) }))}
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </label>
        </div>

        <label className="mt-4 inline-flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={values.enabled}
            onChange={(event) => setValues((current) => ({ ...current, enabled: event.target.checked }))}
          />
          Enabled
        </label>

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <Button type="button" variant="secondary" onClick={onClose}>Cancel</Button>
          <Button
            type="button"
            disabled={!values.name.trim() || !values.emulator_url.trim() || isPending}
            onClick={() => onSubmit(values)}
          >
            {isPending ? 'Saving…' : mode === 'create' ? 'Add Library' : 'Save Changes'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  libraries,
  activeLibraryId,
  enabledCount,
  onSelect,
  onEdit,
  onDelete,
  deletingId,
}: {
  title: string;
  description: string;
  libraries: LibrarySummary[];
  activeLibraryId: string;
  enabledCount: number;
  onSelect: (library: LibrarySummary) => void;
  onEdit: (library: LibrarySummary) => void;
  onDelete: (library: LibrarySummary) => void;
  deletingId: number | null;
}) {
  if (libraries.length === 0) {
    return null;
  }

  return (
    <div className="space-y-3">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
          <p className="text-sm text-slate-400">{description}</p>
        </div>
        <Badge variant="gray">{libraries.length}</Badge>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {libraries.map((library) => {
          const isActive = activeLibraryId === String(library.id);
          const isLastEnabled = library.enabled && enabledCount <= 1;
          const latencyText = library.response_ms !== null ? `${library.response_ms.toFixed(0)} ms` : 'No probe';
          const lastSeenText = library.last_seen_at ? formatDate(library.last_seen_at) : '—';

          return (
            <Card key={library.id} className="bg-quantum-info p-5">
              <div className="flex h-full flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-lg font-semibold text-slate-100">{library.name}</h3>
                      <Badge variant={roleVariant(library.role)}>{toTitleCase(library.role)}</Badge>
                      <Badge variant={statusVariant(library.status)}>{toTitleCase(library.status)}</Badge>
                      {isActive ? <Badge variant="green">Active</Badge> : null}
                      {!library.enabled ? <Badge variant="gray">Disabled</Badge> : null}
                    </div>
                    <p className="mt-2 text-sm text-slate-400">{library.model} · {library.serial_number ?? 'No serial number'}</p>
                    <p className="mt-1 truncate text-xs text-slate-500">{library.emulator_url}</p>
                  </div>
                  <div className="rounded-full border border-quantum-border bg-quantum-panel px-3 py-1 text-xs text-slate-300">
                    {latencyText}
                  </div>
                </div>

                <div className="grid gap-3 md:grid-cols-5">
                  {[
                    ['Drives', String(library.drive_count)],
                    ['Tapes/Slots', `${library.tape_count}/${library.slot_count}`],
                    ['Utilization', `${library.slot_utilization_percent.toFixed(1)}%`],
                    ['Active Jobs', String(library.active_job_count)],
                    ['Alerts', String(library.alerts_count)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3">
                      <div className="text-[11px] uppercase tracking-[0.16em] text-slate-500">{label}</div>
                      <div className="mt-2 text-lg font-semibold text-slate-100">{value}</div>
                    </div>
                  ))}
                </div>

                <div className="grid gap-2 rounded-md border border-quantum-border bg-quantum-panel px-3 py-3 text-sm text-slate-300">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Occupied Slots</span>
                    <span>{library.occupied_slot_count}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Last Seen</span>
                    <span>{lastSeenText}</span>
                  </div>
                </div>

                <div className="mt-auto flex flex-wrap items-center justify-between gap-3">
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" variant={isActive ? 'secondary' : 'primary'} onClick={() => onSelect(library)}>
                      {isActive ? 'Selected' : 'Select'}
                    </Button>
                    <Button type="button" variant="secondary" className="px-3" onClick={() => onEdit(library)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      type="button"
                      variant="danger"
                      className="px-3"
                      disabled={isLastEnabled || deletingId === library.id}
                      onClick={() => onDelete(library)}
                      title={isLastEnabled ? 'At least one enabled library is required' : 'Delete library'}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="flex items-center gap-2 rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-xs text-slate-400">
                    <ArrowUpDown className="h-4 w-4" />
                    Sort #{library.sort_order}
                  </div>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

export default function Libraries() {
  const queryClient = useQueryClient();
  const hasUserSelectedLibrary = useRef(
    typeof window !== 'undefined' && window.localStorage.getItem(USER_CLEARED_LIBRARY_STORAGE_KEY) === '1',
  );
  const [activeLibraryId, setActiveLibraryIdState] = useState(() => getActiveLibraryId());
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<LibrarySort>('status');
  const [modalMode, setModalMode] = useState<'create' | 'edit' | null>(null);
  const [editingLibrary, setEditingLibrary] = useState<LibrarySummary | null>(null);
  const librariesQuery = useQuery({ queryKey: ['libraries'], queryFn: listLibraries, refetchInterval: 30_000 });

  useEffect(() => subscribeActiveLibrary(setActiveLibraryIdState), []);

  const activateLibrary = (library: Pick<LibrarySummary, 'id' | 'name' | 'role'>) => {
    hasUserSelectedLibrary.current = false;
    window.localStorage.removeItem(USER_CLEARED_LIBRARY_STORAGE_KEY);
    setActiveLibraryId(String(library.id), library.name, library.role);
  };

  useEffect(() => {
    const firstEnabledLibrary = (librariesQuery.data ?? []).find((library) => library.enabled) ?? librariesQuery.data?.[0];
    if (!activeLibraryId && firstEnabledLibrary && !hasUserSelectedLibrary.current) {
      activateLibrary(firstEnabledLibrary);
    }
  }, [activeLibraryId, librariesQuery.data]);

  const createMutation = useMutation({
    mutationFn: (values: LibraryFormValues) => createLibrary(toPayload(values)),
    onSuccess: async (library) => {
      setModalMode(null);
      await queryClient.invalidateQueries({ queryKey: ['libraries'] });
      activateLibrary(library);
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, values }: { id: number; values: LibraryFormValues }) => updateLibrary(id, toPayload(values)),
    onSuccess: async (library) => {
      setModalMode(null);
      setEditingLibrary(null);
      await queryClient.invalidateQueries({ queryKey: ['libraries'] });
      if (String(library.id) === activeLibraryId) {
        if (!library.enabled) {
          // Active library was disabled — auto-switch to the next enabled library
          const allLibraries = librariesQuery.data ?? [];
          const next = allLibraries.find((l) => String(l.id) !== activeLibraryId && l.enabled);
          if (next) {
            activateLibrary(next);
          } else {
            setActiveLibraryId('', '', '');
          }
        } else {
          activateLibrary(library);
        }
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteLibrary(id),
    onSuccess: async (_, deletedId) => {
      const remainingLibraries = (librariesQuery.data ?? []).filter((library) => library.id !== deletedId);
      const nextActiveLibrary = remainingLibraries.find((library) => library.enabled) ?? remainingLibraries[0];
      if (String(deletedId) === activeLibraryId) {
        if (nextActiveLibrary) {
          activateLibrary(nextActiveLibrary);
        } else {
          setActiveLibraryId('', '', '');
        }
      }
      await queryClient.invalidateQueries({ queryKey: ['libraries'] });
    },
  });

  if (librariesQuery.isLoading && !librariesQuery.data) {
    return <Spinner />;
  }

  if (librariesQuery.isError) {
    return <ErrorMessage error={librariesQuery.error} onRetry={() => void librariesQuery.refetch()} />;
  }

  const libraries = librariesQuery.data ?? [];
  const enabledCount = libraries.filter((library) => library.enabled).length;
  const activeLibrary = libraries.find((library) => String(library.id) === activeLibraryId) ?? null;
  const filteredLibraries = libraries.filter((library) => {
    const needle = search.trim().toLowerCase();
    if (!needle) {
      return true;
    }
    return [
      library.name,
      library.role,
      library.model,
      library.emulator_url,
      library.serial_number ?? '',
    ].some((value) => value.toLowerCase().includes(needle));
  });
  const sortedLibraries = [...filteredLibraries].sort((left, right) => compareLibraries(left, right, sortBy, activeLibraryId));
  const onlineLibraries = sortedLibraries.filter((library) => library.enabled && library.status === 'online');
  const offlineOrErrorLibraries = sortedLibraries.filter((library) => library.enabled && library.status !== 'online');
  const disabledLibraries = sortedLibraries.filter((library) => !library.enabled);
  const nextSortOrder = (libraries.reduce((highest, library) => Math.max(highest, library.sort_order), -1)) + 1;

  const handleSelect = (library: LibrarySummary) => {
    activateLibrary(library);
  };

  const handleClearSelection = () => {
    hasUserSelectedLibrary.current = true;
    window.localStorage.setItem(USER_CLEARED_LIBRARY_STORAGE_KEY, '1');
    setActiveLibraryId('', '', '');
  };

  const handleDelete = (library: LibrarySummary) => {
    if (!window.confirm(`Delete ${library.name}?`)) {
      return;
    }
    deleteMutation.mutate(library.id);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Library Control Plane</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Operator library grid</h1>
          <p className="mt-1 text-sm text-slate-400">
            Provision, probe, sort, and select active libraries for scoped AML workflows.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="blue">{libraries.length} libraries</Badge>
          <Button type="button" variant="secondary" onClick={() => void librariesQuery.refetch()}>
            <RefreshCw className={cn('mr-2 h-4 w-4', librariesQuery.isFetching && 'animate-spin')} />
            Refresh
          </Button>
          <Button type="button" onClick={() => {
            setEditingLibrary(null);
            setModalMode('create');
          }}>
            <Plus className="mr-2 h-4 w-4" />
            Add Library
          </Button>
        </div>
      </div>

      {activeLibrary ? (
        <Card className="border-blue-500/20 bg-blue-500/5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-blue-200/70">Active Library</div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <h2 className="text-xl font-semibold text-slate-100">{activeLibrary.name}</h2>
                <Badge variant={roleVariant(activeLibrary.role)}>{toTitleCase(activeLibrary.role)}</Badge>
                <Badge variant={statusVariant(activeLibrary.status)}>{toTitleCase(activeLibrary.status)}</Badge>
              </div>
            </div>
            <Button type="button" variant="secondary" onClick={handleClearSelection}>
              Clear Selection
            </Button>
          </div>
        </Card>
      ) : null}

      {(createMutation.isError || updateMutation.isError || deleteMutation.isError) ? (
        <ErrorMessage
          error={createMutation.error ?? updateMutation.error ?? deleteMutation.error}
          onRetry={() => void librariesQuery.refetch()}
        />
      ) : null}

      <Card className="bg-quantum-panel p-5">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr),220px,200px]">
          <label className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search libraries, roles, serials, or endpoints"
              className="w-full rounded-md border border-quantum-border bg-quantum-sidebar py-2 pl-9 pr-3 text-sm text-white outline-none focus:border-quantum-red"
            />
          </label>
          <select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value as LibrarySort)}
            className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white outline-none focus:border-quantum-red"
          >
            <option value="status">By Status</option>
            <option value="name">By Name</option>
            <option value="sort_order">By Sort Order</option>
          </select>
          <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-300">
            {librariesQuery.isFetching ? 'Refreshing probes…' : 'Probe data live'}
          </div>
        </div>
      </Card>

      <Section
        title="Online Libraries"
        description="Enabled endpoints responding to live health probes."
        libraries={onlineLibraries}
        activeLibraryId={activeLibraryId}
        enabledCount={enabledCount}
        onSelect={handleSelect}
        onEdit={(library) => {
          setEditingLibrary(library);
          setModalMode('edit');
        }}
        onDelete={handleDelete}
        deletingId={deleteMutation.variables ?? null}
      />

      <Section
        title="Offline / Error Libraries"
        description="Enabled libraries needing attention before they can be used for operations."
        libraries={offlineOrErrorLibraries}
        activeLibraryId={activeLibraryId}
        enabledCount={enabledCount}
        onSelect={handleSelect}
        onEdit={(library) => {
          setEditingLibrary(library);
          setModalMode('edit');
        }}
        onDelete={handleDelete}
        deletingId={deleteMutation.variables ?? null}
      />

      <Section
        title="Disabled Libraries"
        description="Provisioned libraries kept out of active operator workflows."
        libraries={disabledLibraries}
        activeLibraryId={activeLibraryId}
        enabledCount={enabledCount}
        onSelect={handleSelect}
        onEdit={(library) => {
          setEditingLibrary(library);
          setModalMode('edit');
        }}
        onDelete={handleDelete}
        deletingId={deleteMutation.variables ?? null}
      />

      {modalMode === 'create' ? (
        <LibraryFormModal
          key="create"
          mode="create"
          initialValues={emptyForm(nextSortOrder)}
          isPending={createMutation.isPending}
          onClose={() => setModalMode(null)}
          onSubmit={(values) => createMutation.mutate(values)}
        />
      ) : null}

      {modalMode === 'edit' && editingLibrary ? (
        <LibraryFormModal
          key={`edit-${editingLibrary.id}`}
          mode="edit"
          initialValues={toFormValues(editingLibrary)}
          isPending={updateMutation.isPending}
          onClose={() => {
            setModalMode(null);
            setEditingLibrary(null);
          }}
          onSubmit={(values) => updateMutation.mutate({ id: editingLibrary.id, values })}
        />
      ) : null}
    </div>
  );
}
