type BodyInitLike = BodyInit | FormData | URLSearchParams | Blob;

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

const API_PREFIX = '/api';

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

export async function apiRequest<T>(
  path: string,
  init: Omit<RequestInit, 'body'> & { body?: unknown } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const rawBody = init.body;
  const body =
    rawBody === undefined || rawBody === null || isBodyInitLike(rawBody)
      ? rawBody
      : JSON.stringify(rawBody);

  if (body && !isBodyInitLike(body) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers,
    body,
  });

  const text = await response.text();
  const payload = text ? safeJsonParse(text) : null;

  if (!response.ok) {
    const details =
      typeof payload === 'object' && payload !== null
        ? JSON.stringify(payload, null, 2)
        : text || `${response.status} ${response.statusText}`;
    const message =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String(payload.detail)
        : `Request failed with status ${response.status}`;
    throw new ApiError(
      message,
      response.status,
      `The backend could not complete ${init.method ?? 'GET'} ${path}.`,
      'Check the appliance state, then retry the request.',
      details,
    );
  }

  return payload as T;
}
