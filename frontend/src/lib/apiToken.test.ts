import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  API_TOKEN_REQUIRED_EVENT,
  apiTokenHeaderValue,
  attachApiTokenHeader,
  clearApiToken,
  getApiToken,
  handleUnauthorized,
  isNativeApiUrl,
  resetApiTokenCache,
  setApiToken,
} from './apiToken';

function tokenGateResponse() {
  return { headers: new Headers({ 'WWW-Authenticate': 'Bearer' }) };
}

function amlSessionResponse() {
  return { headers: new Headers() };
}

describe('apiToken', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetApiTokenCache();
  });

  describe('surface classification', () => {
    it('treats the OpenBlade-native surface as native', () => {
      expect(isNativeApiUrl('/api/nas/shares')).toBe(true);
      expect(isNativeApiUrl('/api/pools/p1/files')).toBe(true);
      expect(isNativeApiUrl('/inventory')).toBe(true);
      expect(isNativeApiUrl('http://localhost:8000/jobs/')).toBe(true);
    });

    it('excludes the AML emulator surface, which keeps its own session auth', () => {
      expect(isNativeApiUrl('/aml/users/me')).toBe(false);
      expect(isNativeApiUrl('/aml')).toBe(false);
      expect(isNativeApiUrl('/iblade/system')).toBe(false);
      expect(isNativeApiUrl('http://localhost:8000/aml/partitions?x=1')).toBe(false);
    });

    it('does not mistake a lookalike prefix for the AML surface', () => {
      expect(isNativeApiUrl('/amlfoo/bar')).toBe(true);
    });

    it('mirrors the backend exception for native routes mounted under /aml', () => {
      expect(isNativeApiUrl('/aml/proxy')).toBe(true);
      expect(isNativeApiUrl('/aml/proxy/request')).toBe(true);
    });
  });

  describe('header attachment', () => {
    it('omits the Authorization header when no token is configured', () => {
      const headers = new Headers();
      attachApiTokenHeader(headers, '/api/nas/shares');

      expect(headers.has('Authorization')).toBe(false);
      expect(apiTokenHeaderValue('/api/nas/shares')).toBeNull();
    });

    it('attaches the bearer token on native requests once a token is set', () => {
      setApiToken('tok-native');

      const headers = new Headers();
      attachApiTokenHeader(headers, '/api/nas/shares');

      expect(headers.get('Authorization')).toBe('Bearer tok-native');
    });

    it('never attaches the native token to an AML request', () => {
      setApiToken('tok-native');

      const headers = new Headers();
      attachApiTokenHeader(headers, '/aml/users/me');

      expect(headers.has('Authorization')).toBe(false);
      expect(apiTokenHeaderValue('/aml/users/me')).toBeNull();
    });

    it('leaves a caller-supplied Authorization header alone', () => {
      setApiToken('tok-native');

      const headers = new Headers({ Authorization: 'Basic abc' });
      attachApiTokenHeader(headers, '/api/nas/shares');

      expect(headers.get('Authorization')).toBe('Basic abc');
    });
  });

  describe('storage', () => {
    it('keeps the token in sessionStorage by default', () => {
      setApiToken('tok-session');

      expect(window.sessionStorage.getItem('openblade.api-token')).toBe('tok-session');
      expect(window.localStorage.getItem('openblade.api-token')).toBeNull();
    });

    it('remembers the token on the device when asked', () => {
      setApiToken('tok-remembered', true);

      expect(window.localStorage.getItem('openblade.api-token')).toBe('tok-remembered');
      expect(window.sessionStorage.getItem('openblade.api-token')).toBeNull();
    });

    it('reads a remembered token back on a fresh load', () => {
      window.localStorage.setItem('openblade.api-token', 'tok-remembered');
      resetApiTokenCache();

      expect(getApiToken()).toBe('tok-remembered');
    });

    it('clears both slots', () => {
      window.localStorage.setItem('openblade.api-token', 'tok-remembered');
      window.sessionStorage.setItem('openblade.api-token', 'tok-session');
      resetApiTokenCache();

      clearApiToken();

      expect(getApiToken()).toBeNull();
      expect(window.localStorage.getItem('openblade.api-token')).toBeNull();
      expect(window.sessionStorage.getItem('openblade.api-token')).toBeNull();
    });
  });

  describe('handleUnauthorized', () => {
    it('claims a native 401 from the token gate, clears the token and prompts', () => {
      setApiToken('tok-stale');
      const listener = vi.fn();
      window.addEventListener(API_TOKEN_REQUIRED_EVENT, listener);

      const handled = handleUnauthorized('/api/nas/shares', tokenGateResponse(), {
        error: 'Unauthorized',
      });

      expect(handled).toBe(true);
      expect(getApiToken()).toBeNull();
      expect(listener).toHaveBeenCalledTimes(1);
      expect((listener.mock.calls[0][0] as CustomEvent).detail).toBe('rejected');

      window.removeEventListener(API_TOKEN_REQUIRED_EVENT, listener);
    });

    it('reports "missing" when there was no token to reject', () => {
      const listener = vi.fn();
      window.addEventListener(API_TOKEN_REQUIRED_EVENT, listener);

      const handled = handleUnauthorized('/api/nas/shares', amlSessionResponse(), {
        error: 'Unauthorized',
      });

      expect(handled).toBe(true);
      expect((listener.mock.calls[0][0] as CustomEvent).detail).toBe('missing');

      window.removeEventListener(API_TOKEN_REQUIRED_EVENT, listener);
    });

    it('leaves an AML-session 401 on a native path to the sign-in redirect', () => {
      setApiToken('tok-valid');
      const listener = vi.fn();
      window.addEventListener(API_TOKEN_REQUIRED_EVENT, listener);

      const handled = handleUnauthorized('/api/libraries', amlSessionResponse(), {
        code: 'AML_AUTH_REQUIRED',
      });

      expect(handled).toBe(false);
      expect(getApiToken()).toBe('tok-valid');
      expect(listener).not.toHaveBeenCalled();

      window.removeEventListener(API_TOKEN_REQUIRED_EVENT, listener);
    });

    it('never claims a 401 from the AML surface', () => {
      const listener = vi.fn();
      window.addEventListener(API_TOKEN_REQUIRED_EVENT, listener);

      const handled = handleUnauthorized('/aml/users/me', tokenGateResponse(), {
        error: 'Unauthorized',
      });

      expect(handled).toBe(false);
      expect(listener).not.toHaveBeenCalled();

      window.removeEventListener(API_TOKEN_REQUIRED_EVENT, listener);
    });
  });
});
