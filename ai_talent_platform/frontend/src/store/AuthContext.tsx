import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { request, TOKEN_KEY, REFRESH_KEY, USER_KEY } from '../services/api';
import type { LoginResult, LoginUser } from '../types';

interface AuthContextValue {
  user: LoginUser | null;
  loading: boolean;
  login: (username: string, password: string, remember?: boolean) => Promise<void>;
  logout: () => Promise<void>;
  hasPermission: (permission: string) => boolean;
}
const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<LoginUser | null>(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  });
  const [loading, setLoading] = useState(Boolean(localStorage.getItem(TOKEN_KEY)));

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { setUser(null); setLoading(false); return; }
    let active = true;
    request<LoginUser>({ url: '/auth/me' }).then((me) => {
      if (!active) return;
      setUser(me); localStorage.setItem(USER_KEY, JSON.stringify(me));
    }).catch(() => {
      if (!active) return;
      localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(USER_KEY);
      setUser(null);
    }).finally(() => { if (active) setLoading(false); });
    const onUnauthorized = () => { setUser(null); };
    window.addEventListener('talentos:unauthorized', onUnauthorized);
    return () => { active = false; window.removeEventListener('talentos:unauthorized', onUnauthorized); };
  }, []);

  const login = useCallback(async (username: string, password: string, remember_me = false) => {
    const data = await request<LoginResult>({ method: 'POST', url: '/auth/login', data: { username, password, remember_me } });
    localStorage.setItem(TOKEN_KEY, data.access_token); localStorage.setItem(REFRESH_KEY, data.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user)); setUser(data.user);
  }, []);
  const logout = useCallback(async () => {
    try { await request({ method: 'POST', url: '/auth/logout' }); } catch { /* JWT 前端清理 */ }
    localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(USER_KEY);
    setUser(null);
  }, []);
  const hasPermission = useCallback((permission: string) => Boolean(user && (user.is_superuser || user.permissions.includes('*') || user.permissions.includes(permission))), [user]);
  const value = useMemo(() => ({ user, loading, login, logout, hasPermission }), [user, loading, login, logout, hasPermission]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
