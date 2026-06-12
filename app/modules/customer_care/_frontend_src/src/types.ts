// Backend response shapes — kept thin and non-strict so a missing optional
// field doesn't break the UI.

// Hypershop-integrated shape: backend returns email + full_name + role list.
// `username` retained as a compatibility alias mirrored from `email`.
export interface User {
  id: string;
  email?: string;
  username?: string;
  name?: string | null;
  full_name?: string | null;
  role?: "super_admin" | "admin" | "agent" | "customercare_agent" | "customercare_admin" | string;
  tenant_id?: string;
  status: "online" | "offline" | "busy" | "away" | string;
  must_change_password?: boolean;
  language_skill?: string;
  current_active_chats?: number;
  max_active_chats?: number;
  // CC profile fields (from /me)
  user_id?: string;
}

export interface LoginResponse {
  access_token: string;
  refresh_token?: string;
  refresh_jti?: string;
  token_type?: "bearer" | string;
  must_change_password?: boolean;
  user?: User;
  // Hypershop returns the token list nested OR flat — accept both shapes.
  tokens?: { access_token: string; refresh_token?: string };
}

export interface Conversation {
  id: string;
  tenant_id: string;
  customer_id: string;
  agent_id: string | null;
  channel: string;
  status: "open" | "pending" | "resolved" | string;
  source: string;
  last_message: string | null;
  last_message_at: string;
  handover_required: boolean;
  handover_reason: string | null;
  priority: "high" | "normal" | "low" | string;
  first_response_at: string | null;
  resolved_at: string | null;
  sla_first_response_due_at: string | null;
  sla_resolution_due_at: string | null;
  sla_first_response_breached: boolean;
  sla_resolution_breached: boolean;
  created_at: string;
  updated_at: string;
}

export interface Customer {
  id: string;
  name: string | null;
  phone: string;
  preferred_language: string;
  full_address: string | null;
  consent_status: string;
  status: string;
}

export interface Message {
  id: string;
  tenant_id: string;
  conversation_id: string;
  sender_type: "customer" | "agent" | "ai" | string;
  message_type: string;
  message_body: string | null;
  media_url: string | null;
  channel: string;
  whatsapp_message_id: string | null;
  ai_confidence: number | null;
  created_at: string;
}

export interface ReportSummary {
  agents: number;
  customers: number;
  active_conversations: number;
  pending_conversations: number;
  confirmed_orders: number;
  sales_amount: number;
}

export interface CsatReport {
  responses: number;
  avg_score: number | null;
  csat_top_box_pct: number | null;
  window_days: number;
}

export interface SlaReport {
  total_conversations: number;
  first_response_breaches: number;
  resolution_breaches: number;
}

export interface IntegrationStatus {
  require_external_integrations: boolean;
  whatsapp: { connected: boolean; has_app_secret: boolean };
  openai: { connected: boolean; model: string };
  google_sheets: { connected: boolean };
  observability: { sentry: boolean; otel: boolean };
  pii_encryption: boolean;
}

// SSE event payloads
export type InboxEvent =
  | { type: "conversation.new"; conversation_id: string; customer_phone: string; preview: string }
  | { type: "message.received"; conversation_id: string; customer_phone: string; preview: string }
  | { type: "sla.first_response_breach"; conversation_id: string; due_at: string }
  | { type: "sla.resolution_breach"; conversation_id: string; due_at: string }
  | { type: string; [k: string]: unknown };
