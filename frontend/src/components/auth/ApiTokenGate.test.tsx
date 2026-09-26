import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ApiTokenGate from './ApiTokenGate';
import {
  API_TOKEN_REQUIRED_EVENT,
  getApiToken,
  resetApiTokenCache,
  type ApiTokenPromptReason,
} from '../../lib/apiToken';

function requireToken(reason: ApiTokenPromptReason = 'missing') {
  act(() => {
    window.dispatchEvent(new CustomEvent(API_TOKEN_REQUIRED_EVENT, { detail: reason }));
  });
}

describe('ApiTokenGate', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
    resetApiTokenCache();
  });

  it('renders nothing until a 401 from the token gate — auth-disabled sees no login wall', () => {
    render(<ApiTokenGate />);

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.body.textContent).toBe('');
  });

  it('shows the token entry surface after a real 401', () => {
    render(<ApiTokenGate />);

    requireToken('missing');

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('API token required')).toBeTruthy();
    expect(document.body.textContent).toContain('This appliance requires an API token');
  });

  it('uses a curated message for a rejected token and never echoes it', () => {
    window.sessionStorage.setItem('openblade.api-token', 'rejected-token-value');
    resetApiTokenCache();
    render(<ApiTokenGate />);

    requireToken('rejected');

    expect(document.body.textContent).toContain('The stored API token was rejected');
    expect(document.body.innerHTML).not.toContain('rejected-token-value');
  });

  it('stores a session-only token by default and retries the failed navigation', () => {
    const onAccepted = vi.fn();
    render(<ApiTokenGate onAccepted={onAccepted} />);
    requireToken('missing');

    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'entered-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use this token' }));

    expect(window.sessionStorage.getItem('openblade.api-token')).toBe('entered-token');
    expect(window.localStorage.getItem('openblade.api-token')).toBeNull();
    expect(getApiToken()).toBe('entered-token');
    expect(onAccepted).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('remembers the token on this device when asked', () => {
    render(<ApiTokenGate onAccepted={vi.fn()} />);
    requireToken('missing');

    fireEvent.change(screen.getByLabelText('API token'), { target: { value: 'entered-token' } });
    fireEvent.click(screen.getByLabelText('Remember on this device'));
    fireEvent.click(screen.getByRole('button', { name: 'Use this token' }));

    expect(window.localStorage.getItem('openblade.api-token')).toBe('entered-token');
    expect(window.sessionStorage.getItem('openblade.api-token')).toBeNull();
  });

  it('masks the token and never renders or logs it', () => {
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ApiTokenGate onAccepted={vi.fn()} />);
    requireToken('missing');

    const input = screen.getByLabelText('API token') as HTMLInputElement;
    expect(input.type).toBe('password');

    fireEvent.change(input, { target: { value: 'super-secret-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'Use this token' }));

    expect(document.body.innerHTML).not.toContain('super-secret-token');
    for (const spy of [logSpy, warnSpy, errorSpy]) {
      for (const call of spy.mock.calls) {
        expect(JSON.stringify(call)).not.toContain('super-secret-token');
      }
      spy.mockRestore();
    }
  });
});
