import { LoaderCircle, Pencil, Plus, RefreshCw, X } from 'lucide-react';
import { useEffect, useMemo, useState, type ChangeEvent, type FormEvent, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { getActiveLibraryId, setActiveLibraryId, subscribeActiveLibrary } from '../lib/activeLibrary';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import { probeLibrary, type LibraryStatus } from '../lib/libraryClient';
import { addLibrary, getLibraries, removeLibrary, updateLibrary, type LibraryEntry } from '../lib/libraryStore';
import { formatDate, formatDuration } from '../lib/utils';

interface LibraryFormState {
  name: string;
  host: string;
  port: string;
  username: string;
  password: string;
}

const emptyForm: LibraryFormState = {
  name: '',
  host: '',
  port: '8000',
  username: '',
  password: '',
};

function statusAppearance(status: LibraryEntry['status'] | LibraryStatus['status'] | undefined): {
  label: string;
  icon: string;
  className: string;
} {
  switch (status) {
    case 'online':
      return {
        label: 'Online',
        icon: '🟢',
        className: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
      };
    case 'offline':
      return {
        label: 'Offline',
        icon: '🔴',
        className: 'border-red-500/30 bg-red-500/15 text-red-300',
      };
    case 'error':
      return {
        label: 'Error',
        icon: '🟡',
        className: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
      };
    default:
      return {
        label: 'Unknown',
        icon: '⚪',
        className: 'border-slate-600 bg-slate-800/90 text-slate-300',
      };
  }
}

function getStatsSummary(status?: LibraryStatus): string {
  if (!status?.health) {
    return 'Slots: —/— | Drives: — | Jobs: —';
  }

  return `Slots: ${status.health.slotsUsed}/${status.health.slotsTotal} | Drives: ${status.health.drivesOnline} | Jobs: ${status.health.activeJobs}`;
}

function updateLibraryState(entries: LibraryEntry[], id: string, patch: Partial<LibraryEntry>): LibraryEntry[] {
  return entries.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry));
}

function toFormState(entry?: LibraryEntry): LibraryFormState {
  if (!entry) {
    return emptyForm;
  }

  return {
    name: entry.name,
    host: entry.host,
    port: String(entry.port),
    username: entry.username,
    password: entry.password,
  };
}

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

export default function Libraries() {
  const navigate = useNavigate();
  const [libraries, setLibraries] = useState<LibraryEntry[]>(() => getLibraries());
  const [statuses, setStatuses] = useState<Record<string, LibraryStatus>>({});
  const [probingIds, setProbingIds] = useState<string[]>([]);
  const [editingLibrary, setEditingLibrary] = useState<LibraryEntry | null>(null);
  const [showLibraryModal, setShowLibraryModal] = useState(false);
  const [form, setForm] = useState<LibraryFormState>(emptyForm);
  const [selectedRemoteId, setSelectedRemoteId] = useState<string | null>(null);
  const [activeLibraryId, setActiveLibraryIdState] = useState(() => getActiveLibraryId());

  const mergedLibraries = useMemo(
    () => libraries.map((entry) => ({ entry, status: statuses[entry.id] })),
    [libraries, statuses],
  );

  const selectedRemote = useMemo(
    () => mergedLibraries.find(({ entry }) => entry.id === selectedRemoteId)?.entry,
    [mergedLibraries, selectedRemoteId],
  );
  const selectedRemoteStatus = selectedRemoteId ? statuses[selectedRemoteId] : undefined;

  useEffect(() => subscribeActiveLibrary(setActiveLibraryIdState), []);

  useEffect(() => {
    const localLibrary = libraries.find((entry) => entry.isLocal);
    if (!localLibrary) {
      return;
    }

    if (!activeLibraryId || !libraries.some((entry) => entry.id === activeLibraryId)) {
      setActiveLibraryId(localLibrary.id);
    }
  }, [activeLibraryId, libraries]);

  const setProbeActive = (id: string, active: boolean) => {
    setProbingIds((current) => {
      if (active) {
        return current.includes(id) ? current : [...current, id];
      }

      return current.filter((value) => value !== id);
    });
  };

  const applyProbeResult = (entry: LibraryEntry, status: LibraryStatus) => {
    const lastSeen = status.status === 'online' ? new Date().toISOString() : entry.lastSeen;
    const patch: Partial<LibraryEntry> = {
      status: status.status,
      lastSeen,
    };

    updateLibrary(entry.id, patch);
    setStatuses((current) => ({ ...current, [entry.id]: status }));
    setLibraries((current) => updateLibraryState(current, entry.id, patch));
  };

  const probeEntry = async (entry: LibraryEntry) => {
    setProbeActive(entry.id, true);
    try {
      const result = await probeLibrary(entry);
      applyProbeResult(entry, result);
      return result;
    } finally {
      setProbeActive(entry.id, false);
    }
  };

  const refreshAll = async () => {
    await Promise.all(libraries.map((entry) => probeEntry(entry)));
  };

  useEffect(() => {
    void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleFormChange = (event: ChangeEvent<HTMLInputElement>) => {
    const { name, value } = event.target;
    setForm((current) => ({
      ...current,
      [name]: value,
    }));
  };

  const closeLibraryModal = () => {
    setShowLibraryModal(false);
    setEditingLibrary(null);
    setForm(emptyForm);
  };

  const openAddModal = () => {
    setEditingLibrary(null);
    setForm(emptyForm);
    setShowLibraryModal(true);
  };

  const openEditModal = (entry: LibraryEntry) => {
    setSelectedRemoteId(null);
    setEditingLibrary(entry);
    setForm(toFormState(entry));
    setShowLibraryModal(true);
  };

  const handleSaveLibrary = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    const patch = {
      name: form.name,
      host: form.host,
      port: Number(form.port) || 8000,
      username: form.username,
      password: form.password,
      isLocal: false,
      status: 'unknown' as const,
    };

    if (editingLibrary) {
      const updated: LibraryEntry = {
        ...editingLibrary,
        ...patch,
      };
      updateLibrary(editingLibrary.id, patch);
      setLibraries(getLibraries());
      setStatuses((current) => ({
        ...current,
        [editingLibrary.id]: {
          ...(current[editingLibrary.id] ?? {
            id: editingLibrary.id,
            name: updated.name,
            host: updated.host,
            status: 'offline' as const,
          }),
          id: editingLibrary.id,
          name: updated.name,
          host: updated.host,
        },
      }));
      if (activeLibraryId === editingLibrary.id) {
        setActiveLibraryId(editingLibrary.id);
      }
      closeLibraryModal();
      await probeEntry(updated);
      return;
    }

    const created = addLibrary(patch);
    setLibraries(getLibraries());
    closeLibraryModal();
    await probeEntry(created);
  };

  const handleRemoveLibrary = (entry: LibraryEntry) => {
    const localLibrary = libraries.find((library) => library.isLocal);
    removeLibrary(entry.id);
    setLibraries(getLibraries());
    setStatuses((current) => {
      const next = { ...current };
      delete next[entry.id];
      return next;
    });
    if (selectedRemoteId === entry.id) {
      setSelectedRemoteId(null);
    }
    if (activeLibraryId === entry.id && localLibrary) {
      setActiveLibraryId(localLibrary.id);
    }
  };

  const handleConnect = (entry: LibraryEntry) => {
    setActiveLibraryId(entry.id);
    if (entry.isLocal) {
      navigate('/library');
      return;
    }

    navigate('/dashboard');
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Overview</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Library Grid</h1>
          <p className="mt-1 text-sm text-slate-400">Monitor local and remote Quantum Scalar libraries from one control surface.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" onClick={() => void refreshAll()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh All
          </Button>
          <Button type="button" onClick={openAddModal}>
            <Plus className="mr-2 h-4 w-4" />
            Add Library
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {mergedLibraries.map(({ entry, status }) => {
          const appearance = statusAppearance(status?.status ?? entry.status);
          const isActive = activeLibraryId === entry.id;
          const isProbing = probingIds.includes(entry.id);
          const displayHost = entry.isLocal ? `${entry.host} (same host)` : `${entry.host}:${entry.port}`;

          return (
            <Card key={entry.id} className="bg-quantum-info p-5">
              <div className="flex h-full flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-semibold text-slate-100">{entry.name}</h2>
                      {entry.isLocal ? <Badge variant="blue">Local</Badge> : null}
                      {isActive ? <Badge variant="green">Active</Badge> : null}
                    </div>
                    <p className="mt-1 text-sm text-slate-400">{displayHost}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {isProbing ? <LoaderCircle className="h-4 w-4 animate-spin text-quantum-red" /> : null}
                    <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-semibold ${appearance.className}`}>
                      <span>{appearance.icon}</span>
                      {appearance.label}
                    </span>
                  </div>
                </div>

                <div className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3 text-sm text-slate-200">
                  {getStatsSummary(status)}
                </div>

                <div className="grid gap-2 rounded-md border border-quantum-border bg-quantum-panel px-3 py-3 text-sm text-slate-300">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Last seen</span>
                    <span>{entry.lastSeen ? formatDate(entry.lastSeen) : 'Never'}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Hostname</span>
                    <span className="truncate text-right">{status?.systemInfo?.hostname ?? '—'}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Version</span>
                    <span>{status?.systemInfo?.version ?? '—'}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Uptime</span>
                    <span>{status?.systemInfo ? formatDuration(status.systemInfo.uptime) : '—'}</span>
                  </div>
                </div>

                {status?.error ? <p className="text-sm text-amber-300">{status.error}</p> : <div className="min-h-5" />}

                <div className="mt-auto flex flex-wrap gap-2">
                  <Button type="button" variant="secondary" onClick={() => handleConnect(entry)}>
                    Connect
                  </Button>
                  {!entry.isLocal ? (
                    <>
                      <Button type="button" variant="ghost" onClick={() => setSelectedRemoteId(entry.id)}>
                        Details
                      </Button>
                      <Button type="button" variant="ghost" onClick={() => openEditModal(entry)}>
                        <Pencil className="mr-2 h-4 w-4" />
                        Edit
                      </Button>
                      <Button type="button" variant="danger" onClick={() => handleRemoveLibrary(entry)}>
                        Remove
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      {showLibraryModal ? (
        <Modal onClose={closeLibraryModal}>
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Library Credentials</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-100">{editingLibrary ? 'Edit Library' : 'Add Library'}</h2>
            <p className="mt-1 text-sm text-slate-400">Store AML endpoint credentials for a remote physical library.</p>
          </div>

          <form className="mt-6 grid gap-4" onSubmit={(event) => void handleSaveLibrary(event)}>
            <div className="grid gap-4 md:grid-cols-2">
              <label className="grid gap-2 text-sm text-slate-300">
                <span>Name</span>
                <input
                  className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                  name="name"
                  value={form.name}
                  onChange={handleFormChange}
                  required
                />
              </label>
              <label className="grid gap-2 text-sm text-slate-300">
                <span>Host</span>
                <input
                  className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                  name="host"
                  value={form.host}
                  onChange={handleFormChange}
                  required
                />
              </label>
              <label className="grid gap-2 text-sm text-slate-300">
                <span>Port</span>
                <input
                  className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                  name="port"
                  type="number"
                  min="1"
                  value={form.port}
                  onChange={handleFormChange}
                  required
                />
              </label>
              <label className="grid gap-2 text-sm text-slate-300">
                <span>Username</span>
                <input
                  className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                  name="username"
                  value={form.username}
                  onChange={handleFormChange}
                  required
                />
              </label>
            </div>
            <label className="grid gap-2 text-sm text-slate-300">
              <span>Password</span>
              <input
                className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                name="password"
                type="password"
                value={form.password}
                onChange={handleFormChange}
                required
              />
            </label>

            <div className="flex flex-wrap justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" onClick={closeLibraryModal}>
                Cancel
              </Button>
              <Button type="submit">{editingLibrary ? 'Update Library' : 'Save Library'}</Button>
            </div>
          </form>
        </Modal>
      ) : null}

      {selectedRemote ? (
        <Modal onClose={() => setSelectedRemoteId(null)}>
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Remote Library</div>
            <h2 className="mt-1 text-xl font-semibold text-slate-100">{selectedRemote.name}</h2>
            <p className="mt-1 text-sm text-slate-400">Remote AML-compatible endpoint {selectedRemote.host}:{selectedRemote.port}</p>
          </div>

          <div className="mt-6 grid gap-4 md:grid-cols-2">
            <Card className="bg-quantum-panel">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Connection</div>
              <div className="mt-3 space-y-2 text-sm text-slate-200">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Host</span>
                  <span>{selectedRemote.host}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Port</span>
                  <span>{selectedRemote.port}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Username</span>
                  <span>{selectedRemote.username}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Status</span>
                  <span>{statusAppearance(selectedRemoteStatus?.status ?? selectedRemote.status).label}</span>
                </div>
              </div>
            </Card>

            <Card className="bg-quantum-panel">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Probe Summary</div>
              <div className="mt-3 space-y-2 text-sm text-slate-200">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Hostname</span>
                  <span>{selectedRemoteStatus?.systemInfo?.hostname ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Version</span>
                  <span>{selectedRemoteStatus?.systemInfo?.version ?? '—'}</span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-slate-500">Uptime</span>
                  <span>{selectedRemoteStatus?.systemInfo ? formatDuration(selectedRemoteStatus.systemInfo.uptime) : '—'}</span>
                </div>
                <div className="text-slate-300">{getStatsSummary(selectedRemoteStatus)}</div>
              </div>
            </Card>
          </div>

          {selectedRemoteStatus?.error ? <p className="mt-4 text-sm text-amber-300">{selectedRemoteStatus.error}</p> : null}

          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => void probeEntry(selectedRemote)}>
              Probe Again
            </Button>
            <Button type="button" variant="ghost" onClick={() => openEditModal(selectedRemote)}>
              <Pencil className="mr-2 h-4 w-4" />
              Edit
            </Button>
            <Button type="button" onClick={() => setSelectedRemoteId(null)}>
              Close
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
