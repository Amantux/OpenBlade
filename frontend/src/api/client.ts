import { clearStoredUsername, notifyAuthRedirect } from '../lib/auth';

type BodyInitLike = BodyInit | FormData | URLSearchParams | Blob;

type ApiNamespace = 'aml' | 'root';

interface ApiRequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  namespace?: ApiNamespace;
  skipAuthRedirect?: boolean;
  libraryId?: string;
}

export class ApiError extends Error {
  status: number;
  impact: string;
  action: string;
  details?: string;

  constructor(message: string, status: number, impact: string, action: string, details?: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.impact = impact;
    this.action = action;
    this.details = details;
  }
}

const API_PREFIX = '/aml';
const INITIAL_ACTIVE_LIBRARY_ID = typeof window !== 'undefined'
  ? window.localStorage.getItem('openblade.active-library-id') ?? ''
  : '';

export const activeLibraryIdRef: { current: string } = { current: INITIAL_ACTIVE_LIBRARY_ID };

function isBodyInitLike(value: unknown): value is BodyInitLike {
  return (
    typeof value === 'string' ||
    value instanceof FormData ||
    value instanceof URLSearchParams ||
    value instanceof Blob
  );
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function buildUrl(path: string, namespace: ApiNamespace): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }

  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  if (normalizedPath.startsWith('/aml') || normalizedPath.startsWith('/api')) {
    return normalizedPath;
  }

  return namespace === 'root' ? `/api${normalizedPath}` : `${API_PREFIX}${normalizedPath}`;
}

function redirectToLogin(): void {
  if (typeof window === 'undefined') {
    return;
  }

  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const redirect = encodeURIComponent(currentPath || '/');

  if (!window.location.pathname.startsWith('/login')) {
    clearStoredUsername();
    notifyAuthRedirect();
    window.location.assign(`/login?redirect=${redirect}`);
  }
}

export async function apiRequest<T>(
  path: string,
  init: ApiRequestOptions = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const rawBody = init.body;
  const body =
    rawBody === undefined || rawBody === null || isBodyInitLike(rawBody)
      ? rawBody
      : JSON.stringify(rawBody);

  if (rawBody !== undefined && rawBody !== null && !isBodyInitLike(rawBody) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const namespace = init.namespace ?? 'aml';
  const resolvedLibraryId = init.libraryId ?? activeLibraryIdRef.current;
  if (namespace === 'aml' && resolvedLibraryId && resolvedLibraryId !== 'all') {
    headers.set('X-OpenBlade-Library-Id', resolvedLibraryId);
  }

  const response = await fetch(buildUrl(path, namespace), {
    ...init,
    headers,
    body,
    credentials: 'include',
  });

  const text = await response.text();
  const payload = text ? safeJsonParse(text) : null;

  if (response.status === 401 && !init.skipAuthRedirect) {
    redirectToLogin();
  }

  if (!response.ok) {
    const details =
      typeof payload === 'object' && payload !== null
        ? JSON.stringify(payload, null, 2)
        : text || `${response.status} ${response.statusText}`;
    const message =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String(payload.detail)
        : typeof payload === 'object' && payload !== null && 'summary' in payload
          ? String(payload.summary)
          : `Request failed with status ${response.status}`;
    throw new ApiError(
      message,
      response.status,
      `The backend could not complete ${init.method ?? 'GET'} ${path}.`,
      response.status === 401
        ? 'Sign in again to continue using the AML console.'
        : 'Check the appliance state, then retry the request.',
      details,
    );
  }

  return payload as T;
}

export function rootApiRequest<T>(path: string, init: Omit<ApiRequestOptions, 'namespace'> = {}): Promise<T> {
  return apiRequest<T>(path, { ...init, namespace: 'root' });
}
