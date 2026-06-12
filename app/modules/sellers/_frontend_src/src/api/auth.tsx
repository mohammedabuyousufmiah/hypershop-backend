import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError,
  api,
  setUnauthorizedHandler,
  tokenStore,
} from "./client";

interface User {
  id: string;
  email?: string;
  full_name?: string | null;
  roles?: string[];
}

interface AuthCtx {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const reload = useCallback(async () => {
    if (!tokenStore.access()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      // Reuse Hypershop's /me — returns full IAM user. Falls back to
      // /seller/me/dashboard probe to confirm seller-linked.
      const me = await api<User>("GET", "/api/v1/auth/me");
      setUser(me);
    } catch {
      tokenStore.clear();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      tokenStore.clear();
      setUser(null);
      navigate("/login", { replace: true });
    });
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const resp = await api<{
        access_token?: string;
        refresh_token?: string;
        tokens?: { access_token: string; refresh_token?: string };
      }>("POST", "/api/v1/auth/login", { body: { email, password } });
      const access = resp.access_token ?? resp.tokens?.access_token ?? "";
      const refresh = resp.refresh_token ?? resp.tokens?.refresh_token;
      if (!access) throw new ApiError(401, "no access token in response");
      tokenStore.set(access, refresh);
      await reload();
    },
    [reload],
  );

  const logout = useCallback(async () => {
    tokenStore.clear();
    setUser(null);
    navigate("/login", { replace: true });
  }, [navigate]);

  const value = useMemo(
    () => ({ user, loading, login, logout }),
    [user, loading, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}
