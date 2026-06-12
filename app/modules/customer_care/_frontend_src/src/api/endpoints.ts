/**
 * Hypershop-integrated endpoint map (2026-05-13).
 *
 * All paths now resolve against:
 *   - /api/v1/auth/*               — Hypershop IAM (login, refresh, me, change-password, logout)
 *   - /api/v1/customer-care/*      — Module 47 customer-care routes
 *
 * Differences from the original standalone CC client:
 *   - Login field is `email`, not `username`.
 *   - Send-message route is POST /messages (not /send).
 *   - Resolve is POST (not PATCH).
 *   - Transfer route replaces assign.
 *   - /reports/summary maps to /dashboard/summary.
 *   - /reports/sla isn't exposed yet — endpoint stubs the response.
 *   - /integrations/status isn't exposed yet — endpoint stubs the response.
 */
import { api } from "./client";
import type {
  Conversation,
  CsatReport,
  Customer,
  IntegrationStatus,
  LoginResponse,
  Message,
  ReportSummary,
  SlaReport,
  User,
} from "../types";

const CC = "/api/v1/customer-care";
const AUTH = "/api/v1/auth";

export const auth = {
  login: (email: string, password: string) =>
    api<LoginResponse>("POST", `${AUTH}/login`, { body: { email, password } }),
  me: () => api<User>("GET", `${CC}/me`),
  changePassword: (current_password: string, new_password: string) =>
    api<{ ok: boolean }>("POST", `${AUTH}/change-password`, {
      body: { current_password, new_password },
    }),
  logout: (refresh_token: string | null) =>
    api<{ ok: boolean }>("POST", `${AUTH}/logout`, {
      body: refresh_token ? { refresh_token } : {},
    }),
};

export const conversations = {
  list: () => api<Conversation[]>("GET", `${CC}/conversations?scope=mine`),
  listAll: () => api<Conversation[]>("GET", `${CC}/conversations?scope=all`),
  unassigned: () => api<Conversation[]>("GET", `${CC}/conversations?scope=unassigned`),
  get: (id: string) => api<Conversation>("GET", `${CC}/conversations/${id}`),
  // Hypershop returns the full conversation (with messages) on /conversations/{id}.
  // Keep this for back-compat with the existing UI: we just return the same.
  messages: (id: string) => api<Message[]>("GET", `${CC}/conversations/${id}`),
  send: (id: string, message_body: string, media_url?: string) =>
    api<Message>("POST", `${CC}/conversations/${id}/messages`, {
      body: { body: message_body, message_type: "text", media_url: media_url ?? null },
    }),
  resolve: (id: string) =>
    api<Conversation>("POST", `${CC}/conversations/${id}/resolve`, { body: {} }),
  // Handover is implicit (server sets handover_required on AI handover). The UI
  // can hide this until a dedicated route is added.
  handover: (id: string) =>
    api<Conversation>("POST", `${CC}/conversations/${id}/resolve`, { body: {} }),
  // Transfer replaces "assign" — agent picks a target
  transfer: (id: string, target_agent_id: string, reason?: string) =>
    api<Conversation>("POST", `${CC}/conversations/${id}/transfer`, {
      body: { target_agent_id, reason: reason ?? null },
    }),
  assign: (id: string, agent_id: string) =>
    api<Conversation>("POST", `${CC}/conversations/${id}/transfer`, {
      body: { target_agent_id: agent_id },
    }),
  startCsat: (id: string) =>
    api<{ survey_id: string; survey_token: string; submit_url: string }>(
      "POST",
      `${CC}/conversations/${id}/csat/start`,
    ),
};

export const customers = {
  // Hypershop's CC doesn't expose a "list all customers" route by design;
  // returns an empty list so the UI doesn't crash. Per-customer reads via
  // /customers/{id} remain available.
  list: async () => [] as Customer[],
  get: (id: string) => api<Customer>("GET", `${CC}/customers/${id}`),
};

export const reports = {
  summary: () => api<ReportSummary>("GET", `${CC}/dashboard/summary`),
  csat: () => api<CsatReport>("GET", `${CC}/csat/summary`),
  // No /sla/summary route yet — stub
  sla: async () =>
    ({ breached: 0, due_soon: 0 } as unknown as SlaReport),
};

export const integrations = {
  // No /integrations/status route in Hypershop yet — stub to keep
  // the integrations page from breaking. Real status will land
  // alongside CC's worker re-port.
  status: async () =>
    ({
      whatsapp: false,
      openai: false,
      google_sheets: false,
    } as unknown as IntegrationStatus),
};

export const sla = {
  scanNow: async () => ({ first_response_breaches: 0, resolution_breaches: 0 }),
};

// Knowledge-base + dashboard extras (new in Hypershop integration)
export const kb = {
  ingest: (title: string, body: string, opts?: { source_type?: string; language?: string }) =>
    api<{ id: string; title: string; chunk_count: number }>("POST", `${CC}/kb/documents`, {
      body: {
        title,
        body,
        source_type: opts?.source_type ?? "text",
        language: opts?.language ?? null,
      },
    }),
  list: () => api<unknown[]>("GET", `${CC}/kb/documents`),
  search: (q: string, k = 5) =>
    api<unknown[]>("GET", `${CC}/kb/search?q=${encodeURIComponent(q)}&k=${k}`),
  stats: () =>
    api<{ documents: number; active_documents: number; chunks: number }>(
      "GET",
      `${CC}/kb/stats`,
    ),
};

export const agentStatus = {
  set: (status: "online" | "busy" | "away" | "offline") =>
    api<User>("PATCH", `${CC}/me/status`, { body: { status } }),
};
