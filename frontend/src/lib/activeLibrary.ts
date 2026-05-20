const STORAGE_KEY = 'openblade.active-library-id';
const ACTIVE_LIBRARY_CHANGE_EVENT = 'openblade:active-library-change';

interface ActiveLibraryChangeDetail {
  id: string;
}

function dispatchActiveLibraryChange(id: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(
    new CustomEvent<ActiveLibraryChangeDetail>(ACTIVE_LIBRARY_CHANGE_EVENT, {
      detail: { id },
    }),
  );
}

export function getActiveLibraryId(): string {
  if (typeof window === 'undefined') {
    return '';
  }

  return window.localStorage.getItem(STORAGE_KEY) ?? '';
}

export function setActiveLibraryId(id: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, id);
  dispatchActiveLibraryChange(id);
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
