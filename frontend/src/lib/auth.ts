const STORAGE_KEY = 'openblade.username';
const SESSION_PING_PATHS = ['/aml/users/whoami', '/aml/users/me', '/aml/users'] as const;

export const AUTH_REDIRECT_EVENT = 'openblade:auth-redirect';

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

export function notifyAuthRedirect(): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.dispatchEvent(new Event(AUTH_REDIRECT_EVENT));
}

export async function checkSession(): Promise<boolean> {
  if (typeof window === 'undefined') {
    return false;
  }

  for (const path of SESSION_PING_PATHS) {
    try {
      const response = await fetch(path, {
        credentials: 'include',
        headers: { Accept: 'application/json' },
      });

      if (response.status === 200) {
        return true;
      }
      if (response.status === 401) {
        return false;
      }
      if (response.status !== 404) {
        return false;
      }
    } catch {
      return false;
    }
  }

  return false;
}

export async function isAuthenticated(): Promise<boolean> {
  return checkSession();
}
