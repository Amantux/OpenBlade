import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest, rootApiRequest } from './client';
import {
  API_TOKEN_REQUIRED_EVENT,
  clearApiToken,
  getApiToken,
  resetApiTokenCache,
  setApiToken,
} from '../lib/apiToken';

interface FakeResponseInit {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

function fakeResponse({ status = 200, body = {}, headers = {} }: FakeResponseInit = {}) {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    url: '',
    headers: new Headers(headers),
    text: () => Promise.resolve(text),
  };
}

const fetchMock = vi.fn();

function lastRequestHeaders(): Headers {
  const init = fetchMock.mock.calls.at(-1)?.[1] as { headers: Headers };
  return init.headers;
}

describe('apiRequest native bearer token', () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetApiTokenCache();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sends no Authorization header when no token is configured', async () => {
    fetchMock.mockResolvedValue(fakeResponse({ body: { ok: true } }));

    await rootApiRequest('/jobs/');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(lastRequestHeaders().has('Authorization')).toBe(false);
  });

  it('attaches the bearer token to native requests when one is configured', async () => {
    setApiToken('tok-abc');
    fetchMock.mockResolvedValue(fakeResponse({ body: { ok: true } }));

    await rootApiRequest('/jobs/');

    expect(fetchMock.mock.calls[0][0]).toBe('/api/jobs/');
    expect(lastRequestHeaders().get('Authorization')).toBe('Bearer tok-abc');
  });

  it('leaves AML requests on their own session auth', async () => {
    setApiToken('tok-abc');
    fetchMock.mockResolvedValue(fakeResponse({ body: { ok: true } }));

    await apiRequest('/users/me');

    expect(fetchMock.mock.calls[0][0]).toBe('/aml/users/me');
    expect(lastRequestHeaders().has('Authorization')).toBe(false);
  });

  it('clears the token and asks for a new one on a native 401 from the token gate', async () => {
    setApiToken('tok-stale');
    const listener = vi.fn();
    window.addEventListener(API_TOKEN_REQUIRED_EVENT, listener);
    fetchMock.mockResolvedValue(
      fakeResponse({
        status: 401,
        body: { error: 'Unauthorized', detail: 'This endpoint requires a bearer token.' },
        headers: { 'WWW-Authenticate': 'Bearer' },
      }),
    );

    await expect(rootApiRequest('/jobs/')).rejects.toMatchObject({ status: 401 });

    expect(listener).toHaveBeenCalledTimes(1);
    expect(getApiToken()).toBeNull();

    window.removeEventListener(API_TOKEN_REQUIRED_EVENT, listener);
  });

  it('never puts the token in the thrown error or in a log line', async () => {
    setApiToken('super-secret-token');
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    fetchMock.mockResolvedValue(
      fakeResponse({
        status: 401,
        body: { error: 'Unauthorized', detail: 'This endpoint requires a bearer token.' },
        headers: { 'WWW-Authenticate': 'Bearer' },
      }),
    );

    const thrown = await rootApiRequest('/jobs/').catch((error: unknown) => error);

    const serialized = JSON.stringify({
      message: (thrown as Error).message,
      ...(thrown as Record<string, unknown>),
    });
    expect(serialized).not.toContain('super-secret-token');

    for (const spy of [logSpy, warnSpy, errorSpy]) {
      for (const call of spy.mock.calls) {
        expect(JSON.stringify(call)).not.toContain('super-secret-token');
      }
      spy.mockRestore();
    }

    clearApiToken();
  });
});
