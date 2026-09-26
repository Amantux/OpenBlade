/**
 * Native-API bearer token: the browser half of `openblade/api/api_auth.py`.
 *
 * The backend gates the OpenBlade-**native** surface on
 * `Authorization: Bearer <OPENBLADE_API_TOKEN>` and leaves the Quantum AML
 * emulator surface (`/aml/*`, `/iblade/*`) on its own session cookie. This
 * module mirrors that classification and owns the token, so exactly one place
 * decides "does this request carry the bearer header?".
 *
 * Two rules keep auth-disabled deployments untouched:
 *   - no token stored => no header is ever added, so the request is byte-for-byte
 *     what it was before this module existed;
 *   - the token-entry surface only appears after a *real* 401 from the token
 *     gate. Nothing probes, nothing pre-empts, there is no login wall.
 *
 * The token is a credential: it is never logged, never put in a URL, and never
 * rendered back to the operator.
 */

const STORAGE_KEY = 'openblade.api-token';

/** Dispatched when the token gate rejected a request; carries {@link ApiTokenPromptReason}. */
export const API_TOKEN_REQUIRED_EVENT = 'openblade:api-token-required';

/** Why the token screen is being shown. Drives the curated message. */
export type ApiTokenPromptReason = 'missing' | 'rejected';

/**
 * Path prefixes owned by the Quantum AML emulator. Mirrors
 * `AML_SURFACE_PREFIXES` in `api_auth.py`: these keep their cookie session and
 * must never receive the native bearer token.
 */
const AML_SURFACE_PREFIXES = ['/aml', '/iblade'] as const;

/**
 * Native routes that merely happen to be mounted under an AML prefix. Mirrors
 * `NATIVE_PATHS_UNDER_AML_PREFIX` in `api_auth.py` — the prefix is a mount
 * point, not a statement about ownership, and these ARE token-gated.
 */
const NATIVE_PATHS_UNDER_AML_PREFIX = ['/aml/proxy'] as const;

let cachedToken: string | null | undefined;

function hasWindow(): boolean {
  return typeof window !== 'undefined';
}

/** Segment-aware so `/amlfoo` is not mistaken for the `/aml` surface. */
function hasPrefix(path: string, prefix: string): boolean {
  return path === prefix || path.startsWith(`${prefix}/`);
}

function pathOf(url: string): string {
  if (/^https?:\/\//i.test(url)) {
    try {
      return new URL(url).pathname;
    } catch {
      return url;
    }
  }

  const withoutQuery = url.split(/[?#]/, 1)[0] ?? url;
  return withoutQuery.startsWith('/') ? withoutQuery : `/${withoutQuery}`;
}

/**
 * True when `url` targets the OpenBlade-native surface, i.e. the surface the
 * bearer token authenticates. AML/iBlade URLs return false.
 */
export function isNativeApiUrl(url: string): boolean {
  const path = pathOf(url);
  if (NATIVE_PATHS_UNDER_AML_PREFIX.some((prefix) => hasPrefix(path, prefix))) {
    return true;
  }
  return !AML_SURFACE_PREFIXES.some((prefix) => hasPrefix(path, prefix));
}

/**
 * The stored token, or null when auth is off / no token has been entered.
 * Session storage wins over local storage so a "just this tab" entry can shadow
 * a stale remembered one.
 */
export function getApiToken(): string | null {
  if (cachedToken !== undefined) {
    return cachedToken;
  }
  if (!hasWindow()) {
    return null;
  }

  const stored =
    window.sessionStorage.getItem(STORAGE_KEY) ?? window.localStorage.getItem(STORAGE_KEY);
  cachedToken = stored && stored.trim() ? stored.trim() : null;
  return cachedToken;
}

/**
 * Store the token. `remember` picks the device-persistent slot (localStorage);
 * the default is session-only, so closing the tab forgets the credential.
 */
export function setApiToken(token: string, remember = false): void {
  const trimmed = token.trim();
  if (!trimmed) {
    clearApiToken();
    return;
  }

  cachedToken = trimmed;
  if (!hasWindow()) {
    return;
  }

  // Written to exactly one slot, and the other is cleared, so there is never a
  // second copy to revoke later.
  if (remember) {
    window.localStorage.setItem(STORAGE_KEY, trimmed);
    window.sessionStorage.removeItem(STORAGE_KEY);
  } else {
    window.sessionStorage.setItem(STORAGE_KEY, trimmed);
    window.localStorage.removeItem(STORAGE_KEY);
  }
}

/** Forget the token everywhere. Called before every re-prompt. */
export function clearApiToken(): void {
  cachedToken = null;
  if (!hasWindow()) {
    return;
  }
  window.sessionStorage.removeItem(STORAGE_KEY);
  window.localStorage.removeItem(STORAGE_KEY);
}

/** Test seam: drop the in-memory cache so the next read re-reads storage. */
export function resetApiTokenCache(): void {
  cachedToken = undefined;
}

/**
 * The `Authorization` value for a native request, or null when none applies
 * (AML URL, or no token configured). For transports that are not `Headers`
 * based, e.g. `XMLHttpRequest.setRequestHeader`.
 */
export function apiTokenHeaderValue(url: string): string | null {
  if (!isNativeApiUrl(url)) {
    return null;
  }

  const token = getApiToken();
  return token ? `Bearer ${token}` : null;
}

/**
 * Attach `Authorization: Bearer <token>` when this is a native request and a
 * token is configured. No token, or an AML URL, leaves `headers` untouched.
 * An Authorization header the caller set itself is never overwritten.
 */
export function attachApiTokenHeader(headers: Headers, url: string): void {
  if (headers.has('Authorization')) {
    return;
  }

  const value = apiTokenHeaderValue(url);
  if (value) {
    headers.set('Authorization', value);
  }
}

/** Ask the UI to show the token-entry surface. */
export function notifyApiTokenRequired(reason: ApiTokenPromptReason): void {
  if (!hasWindow()) {
    return;
  }
  window.dispatchEvent(new CustomEvent(API_TOKEN_REQUIRED_EVENT, { detail: reason }));
}

/**
 * Decide whether a 401 came from the native token gate, and if so clear the bad
 * token and raise the prompt.
 *
 * A native path can 401 from *either* gate: `api_auth.py` answers
 * `{"error": "Unauthorized"}` with `WWW-Authenticate: Bearer`, while the AML
 * session layer answers `{"code": "AML_AUTH_REQUIRED"}`. Only the first is ours;
 * returning false leaves the existing AML sign-in redirect in charge.
 */
export function handleUnauthorized(
  url: string,
  response: Pick<Response, 'headers'>,
  payload: unknown,
): boolean {
  if (!isNativeApiUrl(url)) {
    return false;
  }

  const challenge = response.headers.get('WWW-Authenticate') ?? '';
  const isTokenGate =
    challenge.toLowerCase().includes('bearer') ||
    (typeof payload === 'object' &&
      payload !== null &&
      'error' in payload &&
      (payload as { error?: unknown }).error === 'Unauthorized');

  if (!isTokenGate) {
    return false;
  }

  const hadToken = getApiToken() !== null;
  clearApiToken();
  notifyApiTokenRequired(hadToken ? 'rejected' : 'missing');
  return true;
}

/**
 * Re-run whatever the operator was doing when the gate fired. A reload replays
 * the current URL with the new token in place, so the failed navigation retries
 * itself and no per-page retry plumbing is needed. Separate export so tests can
 * substitute it.
 */
export function reloadForApiToken(): void {
  if (hasWindow()) {
    window.location.reload();
  }
}
