const STORAGE_KEY = 'openblade.active-library-id';
const NAME_STORAGE_KEY = 'openblade.active-library-name';
const ACTIVE_LIBRARY_CHANGE_EVENT = 'openblade:active-library-change';

interface ActiveLibraryChangeDetail {
  id: string;
  name: string;
}

function dispatchActiveLibraryChange(id: string, name: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<ActiveLibraryChangeDetail>(ACTIVE_LIBRARY_CHANGE_EVENT, {
      detail: { id, name },
    }),
  );
}

export function getActiveLibraryId(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem(STORAGE_KEY) ?? '';
}

export function getActiveLibraryName(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem(NAME_STORAGE_KEY) ?? '';
}

export function setActiveLibraryId(id: string, name = ''): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, id);
  if (name) {
    window.localStorage.setItem(NAME_STORAGE_KEY, name);
  }
  dispatchActiveLibraryChange(id, name || getActiveLibraryName());
}

export function subscribeActiveLibrary(listener: (id: string) => void): () => void {
  if (typeof window === 'undefined') {
    return () => undefined;
  }

  const handleChange = (event: Event) => {
    listener((event as CustomEvent<ActiveLibraryChangeDetail>).detail.id);
  };
  const handleStorage = (event: StorageEvent) => {
    if (event.key === STORAGE_KEY) {
      listener(event.newValue ?? '');
    }
  };

  window.addEventListener(ACTIVE_LIBRARY_CHANGE_EVENT, handleChange as EventListener);
  window.addEventListener('storage', handleStorage);

  return () => {
    window.removeEventListener(ACTIVE_LIBRARY_CHANGE_EVENT, handleChange as EventListener);
    window.removeEventListener('storage', handleStorage);
  };
}
