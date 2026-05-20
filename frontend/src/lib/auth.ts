const STORAGE_KEY = 'openblade.username';

export function getStoredUsername(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.localStorage.getItem(STORAGE_KEY);
}

export function storeUsername(username: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, username);
}

export function clearStoredUsername(): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(STORAGE_KEY);
}

export function isAuthenticated(): boolean {
  return Boolean(getStoredUsername());
}
