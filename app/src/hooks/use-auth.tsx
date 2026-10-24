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
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AppUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then(async (me) => {
        if (cancelled) return;
        if (me) {
          setUser(me);
        } else if (__DEV__ && process.env.EXPO_PUBLIC_DEV_AUTOLOGIN === '1') {
          // 开发联调便利：env 开启时用演示账号自动登录（生产打包不含该 env）
          try {
            const data = await apiLogin({ username: 'petsense', password: '123456' });
            if (!cancelled) setUser(data.user);
          } catch {
            // 演示账号不存在或后端不可达时保持未登录
          }
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
    },
    [],
  );

  const logout = useCallback(async () => {
    await apiLogout();
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, loading, login, register, logout }),
    [user, loading, login, register, logout],
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
