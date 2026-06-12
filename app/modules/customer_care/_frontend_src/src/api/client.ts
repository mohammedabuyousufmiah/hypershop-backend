// Lightweight fetch wrapper — JWT injection, auto-refresh on 401, tenant header.

const ACCESS_KEY = "agent-inbox.access";
const REFRESH_KEY = "agent-inbox.refresh";
const TENANT_KEY = "agent-inbox.tenant";

class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}
export { ApiError };

export const tokenStore = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  tenant: () => localStorage.getItem(TENANT_KEY),
  set: (access: string, refresh?: string, tenant?: string) => {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
    if (tenant) localStorage.setItem(TENANT_KEY, tenant);
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(TENANT_KEY);
  },
};

let onUnauthorized: (() => void) | null = null;
export const setUnauthorizedHandler = (cb: () => void) => {
  onUnauthorized = cb;
};

let refreshing: Promise<string | null> | null = null;

async function tryRefresh(): Promise<string | null> {
  const refresh = tokenStore.refresh();
  if (!refresh) return null;
  if (refreshing) return refreshing;
  refreshing = (async () => {
    try {
      const resp = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!resp.ok) return null;
      const body = await resp.json();
      const newAccess = body.access_token as string;
      tokenStore.set(newAccess, refresh, tokenStore.tenant() ?? undefined);
      return newAccess;
    } catch {
      return null;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export interface ApiOpts {
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  raw?: boolean; // if true, returns Response without parsing
}

export async function api<T = unknown>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  opts: ApiOpts = {},
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (opts.query) {
    Object.entries(opts.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    });
  }

  const headers: Record<string, string> = {
    Accept: "application/json",
    ...opts.headers,
  };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";

  const access = tokenStore.access();
  if (access) headers.Authorization = `Bearer ${access}`;
  const tenant = tokenStore.tenant();
  if (tenant) headers["X-Tenant-ID"] = tenant;

  const init: RequestInit = {
    method,
    headers,
    signal: opts.signal,
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  let resp = await fetch(url.toString().replace(window.location.origin, ""), init);

  // One auto-retry after refresh on 401
  if (resp.status === 401 && tokenStore.refresh()) {
    const newAccess = await tryRefresh();
    if (newAccess) {
      headers.Authorization = `Bearer ${newAccess}`;
      resp = await fetch(url.toString().replace(window.location.origin, ""), {
        ...init,
        headers,
      });
    }
  }

  if (resp.status === 401 || resp.status === 403) {
    if (resp.status === 401) tokenStore.clear();
    onUnauthorized?.();
  }

  if (opts.raw) return resp as unknown as T;

  if (!resp.ok) {
    let detail: unknown = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail ?? body;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }

  if (resp.status === 204) return undefined as T;
  const ct = resp.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return (await resp.text()) as unknown as T;
  return (await resp.json()) as T;
}

// SWR-compatible fetcher
export const swrFetcher = <T = unknown>(path: string) => api<T>("GET", path);
