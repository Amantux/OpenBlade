import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthProvider, useAuth } from './auth-context';

const authModule = vi.hoisted(() => ({
  AUTH_REDIRECT_EVENT: 'openblade:auth-redirect',
  checkSession: vi.fn<() => Promise<boolean>>(),
  clearStoredUsername: vi.fn(),
  getStoredUsername: vi.fn<() => string | null>(),
  storeUsername: vi.fn(),
}));

vi.mock('./auth', () => authModule);

function AuthStatusProbe() {
  const auth = useAuth();
  const location = useLocation();

  return (
    <div>
      <div data-testid="auth-state">{auth.isChecking ? 'checking' : auth.isAuthenticated ? 'authenticated' : 'unauthenticated'}</div>
      <div data-testid="username">{auth.username ?? ''}</div>
      <div data-testid="location">{location.pathname}{location.search}</div>
    </div>
  );
}

describe('AuthProvider', () => {
  beforeEach(() => {
    authModule.checkSession.mockReset();
    authModule.clearStoredUsername.mockReset();
    authModule.getStoredUsername.mockReset();
    authModule.storeUsername.mockReset();
  });

  it('hydrates authenticated state from the current session', async () => {
    authModule.checkSession.mockResolvedValue(true);
    authModule.getStoredUsername.mockReturnValue('alice');

    render(
      <MemoryRouter initialEntries={['/dashboard']}>
        <AuthProvider>
          <AuthStatusProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('auth-state').textContent).toBe('authenticated');
    });
    expect(screen.getByTestId('username').textContent).toBe('alice');
    expect(screen.getByTestId('location').textContent).toBe('/dashboard');
  });

  it('redirects to login after an auth redirect event', async () => {
    authModule.checkSession.mockResolvedValue(true);
    authModule.getStoredUsername.mockReturnValue('operator');

    render(
      <MemoryRouter initialEntries={['/library/ie?tab=mail-slot']}>
        <AuthProvider>
          <AuthStatusProbe />
        </AuthProvider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByTestId('auth-state').textContent).toBe('authenticated');
    });

    window.dispatchEvent(new Event(authModule.AUTH_REDIRECT_EVENT));

    await waitFor(() => {
      expect(screen.getByTestId('auth-state').textContent).toBe('unauthenticated');
    });
    expect(authModule.clearStoredUsername).toHaveBeenCalled();
    expect(screen.getByTestId('location').textContent).toBe('/login?redirect=%2Flibrary%2Fie%3Ftab%3Dmail-slot');
  });
});
