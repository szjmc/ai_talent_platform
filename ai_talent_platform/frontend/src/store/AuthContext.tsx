import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { request, isDemoFallback, TOKEN_KEY, REFRESH_KEY, USER_KEY } from '../services/api';
import { demoUser } from '../services/mockData';
import type { LoginResult, LoginUser } from '../types';

interface AuthContextValue {
  user: LoginUser | null;
  loading: boolean;
  demo: boolean;
  login: (username: string, password: string, remember?: boolean) => Promise<void>;
  enterDemo: () => void;
  logout: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<LoginUser | null>(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  });
  const [loading, setLoading] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));
  const [demo, setDemo] = useState(!localStorage.getItem(TOKEN_KEY));

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      if (isDemoFallback && !user) { setUser(demoUser); setDemo(true); }
      setLoading(false); return;
    }
    request<LoginUser>({ url: '/auth/me' }).then((me) => {
      setUser(me); setDemo(false); localStorage.setItem(USER_KEY, JSON.stringify(me));
    }).catch(() => {
      if (isDemoFallback) { setUser(demoUser); setDemo(true); }
    }).finally(() => setLoading(false));
    const onUnauthorized = () => { setUser(isDemoFallback ? demoUser : null); setDemo(isDemoFallback); };
    window.addEventListener('talentos:unauthorized', onUnauthorized);
    return () => window.removeEventListener('talentos:unauthorized', onUnauthorized);
  }, []);

  const login = useCallback(async (username: string, password: string, remember_me = false) => {
    const data = await request<LoginResult>({ method: 'POST', url: '/auth/login', data: { username, password, remember_me } });
    localStorage.setItem(TOKEN_KEY, data.access_token); localStorage.setItem(REFRESH_KEY, data.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user)); setUser(data.user); setDemo(false);
  }, []);
  const enterDemo = useCallback(() => { setUser(demoUser); setDemo(true); setLoading(false); }, []);
  const logout = useCallback(async () => {
    if (!demo) { try { await request({ method: 'POST', url: '/auth/logout' }); } catch { /* JWT 前端清理 */ } }
    localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(USER_KEY);
    setUser(null); setDemo(false);
  }, [demo]);
  const hasPermission = useCallback((permission: string) => Boolean(user && (user.is_superuser || user.permissions.includes('*') || user.permissions.includes(permission))), [user]);
  const value = useMemo(() => ({ user, loading, demo, login, enterDemo, logout, hasPermission }), [user, loading, demo, login, enterDemo, logout, hasPermission]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
