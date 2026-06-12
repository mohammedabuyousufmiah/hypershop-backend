/**
 * Lightweight fetch wrapper for the seller dashboard.
 *
 * - Reads access token from localStorage under `seller.access`
 * - Auto-refreshes on 401 via /api/v1/auth/refresh
 * - Throws ApiError with status + parsed detail on non-2xx
 */

const ACCESS_KEY = "seller.access";
const REFRESH_KEY = "seller.refresh";

export const tokenStore = {
  access: () => localStorage.getItem(ACCESS_KEY),
  refresh: () => localStorage.getItem(REFRESH_KEY),
  set: (access: string, refresh?: string) => {
    localStorage.setItem(ACCESS_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
  },
};

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
  }
}

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
      const r = await fetch("/api/v1/auth/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!r.ok) return null;
      const body = await r.json();
      const newAccess = body.access_token ?? body.tokens?.access_token;
      if (newAccess) tokenStore.set(newAccess, refresh);
      return newAccess ?? null;
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
}

export async function api<T = unknown>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  opts: ApiOpts = {},
): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (opts.query) {
    Object.entries(opts.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "")
        url.searchParams.set(k, String(v));
    });
  }
  const headers: Record<string, string> = { Accept: "application/json" };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const tok = tokenStore.access();
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const init: RequestInit = {
    method,
    headers,
    signal: opts.signal,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  };
  let resp = await fetch(url.toString().replace(window.location.origin, ""), init);
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
  if (!resp.ok) {
    let detail: unknown = `HTTP ${resp.status}`;
    try {
      const body = await resp.json();
      detail = body.detail ?? body.message ?? body;
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const swrFetcher = <T = unknown>(path: string) => api<T>("GET", path);
