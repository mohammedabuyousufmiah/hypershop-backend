import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { setUnauthorizedHandler, tokenStore } from "../api/client";
import { auth } from "../api/endpoints";
import type { User } from "../types";

interface AuthState {
  user: User | null;
  loading: boolean;
  mustChange: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (current: string, next: string) => Promise<void>;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [mustChange, setMustChange] = useState<boolean>(false);

  const reload = useCallback(async () => {
    if (!tokenStore.access()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await auth.me();
      setUser(me);
      setMustChange(Boolean(me.must_change_password));
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      tokenStore.clear();
      setUser(null);
    });
    void reload();
  }, [reload]);

  const login = useCallback(async (username: string, password: string) => {
    // Hypershop accepts email (field renamed but variable kept for back-compat)
    const resp = await auth.login(username, password);
    // Hypershop may return tokens nested in `tokens` OR flat — accept both
    const access = resp.access_token ?? resp.tokens?.access_token ?? "";
    const refresh = resp.refresh_token ?? resp.tokens?.refresh_token;
    tokenStore.set(access, refresh, resp.user?.tenant_id ?? undefined);
    // /me is fetched separately after login (Hypershop login response
    // doesn't include the user object); leave user state to reload().
    setUser(resp.user ?? null);
    setMustChange(Boolean(resp.must_change_password));
    // Trigger an immediate /me reload so the agent profile populates
    void reload();
  }, [reload]);

  const logout = useCallback(async () => {
    try {
      await auth.logout(tokenStore.refresh());
    } catch {
      /* best-effort */
    }
    tokenStore.clear();
    setUser(null);
  }, []);

  const changePassword = useCallback(
    async (current_password: string, new_password: string) => {
      await auth.changePassword(current_password, new_password);
      setMustChange(false);
      await reload();
    },
    [reload],
  );

  const value = useMemo<AuthState>(
    () => ({ user, loading, mustChange, login, logout, changePassword, reload }),
    [user, loading, mustChange, login, logout, changePassword, reload],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within <AuthProvider>");
  return ctx;
}
