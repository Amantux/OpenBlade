import { activeLibraryIdRef } from '../api/client';

const STORAGE_KEY = 'openblade.active-library-id';
const NAME_STORAGE_KEY = 'openblade.active-library-name';
const ROLE_STORAGE_KEY = 'openblade.active-library-role';
const ACTIVE_LIBRARY_CHANGE_EVENT = 'openblade:active-library-change';

interface ActiveLibraryChangeDetail {
  id: string;
  name: string;
  role: string;
}

function dispatchActiveLibraryChange(id: string, name: string, role: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<ActiveLibraryChangeDetail>(ACTIVE_LIBRARY_CHANGE_EVENT, {
      detail: { id, name, role },
    }),
  );
}

export function getActiveLibraryId(): string {
  if (typeof window === 'undefined') {
    return activeLibraryIdRef.current;
  }

  return window.localStorage.getItem(STORAGE_KEY) ?? '';
}

export function getActiveLibraryName(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem(NAME_STORAGE_KEY) ?? '';
}

export function getActiveLibraryRole(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem(ROLE_STORAGE_KEY) ?? '';
}

export function setActiveLibraryRole(role: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  if (role) {
    window.localStorage.setItem(ROLE_STORAGE_KEY, role);
  } else {
    window.localStorage.removeItem(ROLE_STORAGE_KEY);
  }

  dispatchActiveLibraryChange(getActiveLibraryId(), getActiveLibraryName(), role);
}

export function setActiveLibraryId(id: string, name = '', role?: string): void {
  if (typeof window === 'undefined') {
    activeLibraryIdRef.current = id;
    return;
  }

  const previousId = getActiveLibraryId();
  const nextRole = role !== undefined ? role : id && id === previousId ? getActiveLibraryRole() : '';
  activeLibraryIdRef.current = id;

  if (id) {
    window.localStorage.setItem(STORAGE_KEY, id);
  } else {
    window.localStorage.removeItem(STORAGE_KEY);
  }

  if (name) {
    window.localStorage.setItem(NAME_STORAGE_KEY, name);
  } else if (!id) {
    window.localStorage.removeItem(NAME_STORAGE_KEY);
  }

  if (nextRole) {
    window.localStorage.setItem(ROLE_STORAGE_KEY, nextRole);
  } else {
    window.localStorage.removeItem(ROLE_STORAGE_KEY);
  }

  dispatchActiveLibraryChange(id, name || getActiveLibraryName(), nextRole);
}

export function subscribeActiveLibrary(listener: (id: string) => void): () => void {
  if (typeof window === 'undefined') {
    return () => undefined;
  }

  const handleChange = (event: Event) => {
    listener((event as CustomEvent<ActiveLibraryChangeDetail>).detail.id);
  };
  const handleStorage = (event: StorageEvent) => {
    if ([STORAGE_KEY, NAME_STORAGE_KEY, ROLE_STORAGE_KEY].includes(event.key ?? '')) {
      listener(getActiveLibraryId());
    }
  };

  window.addEventListener(ACTIVE_LIBRARY_CHANGE_EVENT, handleChange as EventListener);
  window.addEventListener('storage', handleStorage);

  return () => {
    window.removeEventListener(ACTIVE_LIBRARY_CHANGE_EVENT, handleChange as EventListener);
    window.removeEventListener('storage', handleStorage);
  };
}
