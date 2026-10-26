/**
 * 账号全局状态：启动时用本地 token 换取用户信息，未登录时社区/我的页引导去登录。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import {
  fetchMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
} from '@/services/api';
import { reconnectTelemetry } from '@/services/telemetry-service';
import type { AppUser } from '@/types/social';

type AuthContextValue = {
  user: AppUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (input: {
    username: string;
    displayName: string;
    password: string;
    avatarEmoji: string;
  }) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AppUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        if (me) {
          setUser(me);
          reconnectTelemetry();
        }
      })
      .catch(() => {
        // 网络异常按未登录处理
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await apiLogin({ username, password });
    setUser(data.user);
    // token 已更新，遥测 WS 换新凭证立即重连
    reconnectTelemetry();
  }, []);

  const register = useCallback(
    async (input: {
      username: string;
      displayName: string;
      password: string;
      avatarEmoji: string;
    }) => {
      const data = await apiRegister(input);
      setUser(data.user);
      reconnectTelemetry();
    },
    [],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
    reconnectTelemetry();
  }, []);

  const refreshUser = useCallback(async () => {
    const me = await fetchMe();
    if (me) setUser((current) => (current?.id === me.id ? me : current));
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout, refreshUser }),
    [user, loading, login, register, logout, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth 必须在 <AuthProvider> 内部使用');
  }
  return context;
}
