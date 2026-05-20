import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { AUTH_REDIRECT_EVENT, checkSession, clearStoredUsername, getStoredUsername, storeUsername } from './auth';

type AuthStatus = 'checking' | 'authenticated' | 'unauthenticated';

interface AuthContextValue {
  isAuthenticated: boolean;
  isChecking: boolean;
  username: string | null;
  refreshSession: () => Promise<boolean>;
  markAuthenticated: (username: string) => void;
  markUnauthenticated: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const [status, setStatus] = useState<AuthStatus>('checking');
  const [username, setUsername] = useState<string | null>(() => getStoredUsername());

  const markUnauthenticated = useCallback(() => {
    clearStoredUsername();
    setUsername(null);
    setStatus('unauthenticated');
  }, []);

  const refreshSession = useCallback(async () => {
    setStatus('checking');
    const authenticated = await checkSession();

    if (!authenticated) {
      markUnauthenticated();
      return false;
    }

    setUsername(getStoredUsername());
    setStatus('authenticated');
    return true;
  }, [markUnauthenticated]);

  const markAuthenticated = useCallback((nextUsername: string) => {
    storeUsername(nextUsername);
    setUsername(nextUsername);
    setStatus('authenticated');
  }, []);

  useEffect(() => {
    void refreshSession();
  }, [refreshSession]);

  useEffect(() => {
    function handleUnauthorized() {
      markUnauthenticated();
      if (location.pathname.startsWith('/login')) {
        return;
      }

      const redirect = encodeURIComponent(`${location.pathname}${location.search}${location.hash}` || '/');
      navigate(`/login?redirect=${redirect}`, { replace: true });
    }

    window.addEventListener(AUTH_REDIRECT_EVENT, handleUnauthorized);
    return () => window.removeEventListener(AUTH_REDIRECT_EVENT, handleUnauthorized);
  }, [location.hash, location.pathname, location.search, markUnauthenticated, navigate]);

  const value = useMemo<AuthContextValue>(
    () => ({
      isAuthenticated: status === 'authenticated',
      isChecking: status === 'checking',
      username,
      refreshSession,
      markAuthenticated,
      markUnauthenticated,
    }),
    [markAuthenticated, markUnauthenticated, refreshSession, status, username],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }

  return context;
}
