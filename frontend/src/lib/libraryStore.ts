export interface LibraryEntry {
  id: string;
  name: string;
  host: string;
  port: number;
  username: string;
  password: string;
  isLocal: boolean;
  lastSeen?: string;
  status?: 'online' | 'offline' | 'error' | 'unknown';
}

const STORAGE_KEY = 'openblade.library-grid.entries';
const DEFAULT_PORT = 8000;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function normalizeStatus(value: unknown): LibraryEntry['status'] {
  switch (value) {
    case 'online':
    case 'offline':
    case 'error':
    case 'unknown':
      return value;
    default:
      return 'unknown';
  }
}

function normalizePort(value: unknown): number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : DEFAULT_PORT;
}

function getCurrentHost(): string {
  if (typeof window === 'undefined') {
    return 'localhost';
  }

  return window.location.hostname || 'localhost';
}

function createLocalEntry(existing?: Partial<LibraryEntry>): LibraryEntry {
  return {
    id: typeof existing?.id === 'string' && existing.id ? existing.id : crypto.randomUUID(),
    name: 'Local',
    host: getCurrentHost(),
    port: DEFAULT_PORT,
    username: typeof existing?.username === 'string' ? existing.username : '',
    password: typeof existing?.password === 'string' ? existing.password : '',
    isLocal: true,
    lastSeen: typeof existing?.lastSeen === 'string' ? existing.lastSeen : undefined,
    status: normalizeStatus(existing?.status),
  };
}

function normalizeEntry(value: unknown): LibraryEntry | null {
  if (!isRecord(value)) {
    return null;
  }

  const id = typeof value.id === 'string' && value.id ? value.id : crypto.randomUUID();
  const name = typeof value.name === 'string' && value.name.trim() ? value.name.trim() : '';
  const host = typeof value.host === 'string' && value.host.trim() ? value.host.trim() : '';
  const username = typeof value.username === 'string' ? value.username : '';
  const password = typeof value.password === 'string' ? value.password : '';
  const isLocal = value.isLocal === true;

  if (!name || !host) {
    return null;
  }

  return {
    id,
    name,
    host,
    port: normalizePort(value.port),
    username,
    password,
    isLocal,
    lastSeen: typeof value.lastSeen === 'string' ? value.lastSeen : undefined,
    status: normalizeStatus(value.status),
  };
}

function sortLibraries(entries: LibraryEntry[]): LibraryEntry[] {
  return [...entries].sort((left, right) => {
    if (left.isLocal !== right.isLocal) {
      return left.isLocal ? -1 : 1;
    }

    return left.name.localeCompare(right.name);
  });
}

function persistLibraries(entries: LibraryEntry[]): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
}

function ensureLocalEntry(entries: LibraryEntry[]): LibraryEntry[] {
  const local = entries.find((entry) => entry.isLocal);
  const remoteEntries = entries.filter((entry) => !entry.isLocal);
  return sortLibraries([createLocalEntry(local), ...remoteEntries]);
}

function readStoredLibraries(): LibraryEntry[] {
  if (typeof window === 'undefined') {
    return ensureLocalEntry([]);
  }

  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    const seeded = ensureLocalEntry([]);
    persistLibraries(seeded);
    return seeded;
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    const entries = Array.isArray(parsed)
      ? parsed
          .map((item) => normalizeEntry(item))
          .filter((item): item is LibraryEntry => item !== null)
      : [];
    const normalized = ensureLocalEntry(entries);
    persistLibraries(normalized);
    return normalized;
  } catch {
    const seeded = ensureLocalEntry([]);
    persistLibraries(seeded);
    return seeded;
  }
}

export function getLibraries(): LibraryEntry[] {
  return readStoredLibraries();
}

export function addLibrary(entry: Omit<LibraryEntry, 'id'>): LibraryEntry {
  const created: LibraryEntry = {
    ...entry,
    id: crypto.randomUUID(),
    name: entry.name.trim(),
    host: entry.host.trim(),
    port: normalizePort(entry.port),
    username: entry.username.trim(),
    status: entry.status ?? 'unknown',
  };

  const next = ensureLocalEntry([...readStoredLibraries().filter((library) => library.id !== created.id), created]);
  persistLibraries(next);
  return created;
}

export function updateLibrary(id: string, patch: Partial<LibraryEntry>): void {
  const next = ensureLocalEntry(
    readStoredLibraries().map((entry) => {
      if (entry.id !== id) {
        return entry;
      }

      return {
        ...entry,
        ...patch,
        id: entry.id,
        name: typeof patch.name === 'string' && patch.name.trim() ? patch.name.trim() : entry.name,
        host: typeof patch.host === 'string' && patch.host.trim() ? patch.host.trim() : entry.host,
        port: patch.port === undefined ? entry.port : normalizePort(patch.port),
        username: typeof patch.username === 'string' ? patch.username.trim() : entry.username,
        password: typeof patch.password === 'string' ? patch.password : entry.password,
        isLocal: entry.isLocal,
        status: patch.status === undefined ? entry.status : normalizeStatus(patch.status),
        lastSeen: patch.lastSeen === undefined ? entry.lastSeen : patch.lastSeen,
      };
    }),
  );

  persistLibraries(next);
}

export function removeLibrary(id: string): void {
  const next = ensureLocalEntry(readStoredLibraries().filter((entry) => entry.id !== id || entry.isLocal));
  persistLibraries(next);
}
