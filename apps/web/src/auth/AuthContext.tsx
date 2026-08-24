import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Role } from '@ticket/shared';
import { api, setAccessToken, getAccessToken, ApiClientError } from '../lib/api.js';

export type User = { id: string; email: string; name: string; role: Role };

type AuthState = {
  user: User | null;
  /** True until the stored token has been checked. Routes must wait on this. */
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (input: { email: string; password: string; name: string }) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

/**
 * Auth is exactly what context is for: app-wide, read everywhere, changes
 * rarely. Form field values stay in the components that own them.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // A stored token proves nothing — it may be expired, or signed before the
  // account was deleted. Ask the server who it thinks we are, once, on boot.
  useEffect(() => {
    if (!getAccessToken()) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    api
      .get<{ user: User }>('/api/v1/auth/me')
      .then(({ user }) => {
        if (!cancelled) setUser(user);
      })
      .catch(() => {
        setAccessToken(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const adopt = useCallback((res: { user: User; accessToken: string }) => {
    setAccessToken(res.accessToken);
    setUser(res.user);
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      adopt(await api.post('/api/v1/auth/login', { email, password }));
    },
    [adopt],
  );

  const register = useCallback(
    async (input: { email: string; password: string; name: string }) => {
      adopt(await api.post('/api/v1/auth/register', input));
    },
    [adopt],
  );

  const logout = useCallback(() => {
    setAccessToken(null);
    setUser(null);
  }, []);

  // The API client fires this the first time a request comes back 401. Clearing
  // the user here is what flips the guarded routes back to the login screen,
  // rather than leaving somebody staring at a page whose every button fails.
  useEffect(() => {
    const onExpired = () => setUser(null);
    window.addEventListener('auth:expired', onExpired);
    return () => window.removeEventListener('auth:expired', onExpired);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>');
  return ctx;
}

/** Turns any thrown value into something worth showing a person. */
export function messageFor(err: unknown): string {
  if (err instanceof ApiClientError) return err.message;
  return 'Could not reach the server. Check your connection and try again.';
}
