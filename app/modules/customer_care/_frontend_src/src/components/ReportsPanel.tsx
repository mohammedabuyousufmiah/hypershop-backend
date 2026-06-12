import useSWR from "swr";
import { swrFetcher } from "../api/client";
import type { CsatReport, ReportSummary, SlaReport } from "../types";

export function ReportsPanel() {
  const { data: summary } = useSWR<ReportSummary>("/api/reports/summary", swrFetcher, {
    refreshInterval: 30_000,
  });
  const { data: csat } = useSWR<CsatReport>("/api/reports/csat", swrFetcher, {
    refreshInterval: 60_000,
  });
  const { data: sla } = useSWR<SlaReport>("/api/reports/sla", swrFetcher, {
    refreshInterval: 30_000,
  });

  return (
    <div className="flex flex-col h-full bg-white border-l border-slate-200 p-4 gap-3 overflow-y-auto">
      <h3 className="text-sm font-semibold text-slate-700">Live overview</h3>

      <Stat label="Open conversations" value={summary?.active_conversations ?? "—"} />
      <Stat label="Pending" value={summary?.pending_conversations ?? "—"} accent="warn" />
      <Stat label="Confirmed orders" value={summary?.confirmed_orders ?? "—"} />

      <h3 className="text-sm font-semibold text-slate-700 mt-3">CSAT (last 30d)</h3>
      <Stat label="Responses" value={csat?.responses ?? 0} />
      <Stat label="Avg score" value={csat?.avg_score ?? "—"} accent="ok" />
      <Stat
        label="Top-box %"
        value={csat?.csat_top_box_pct != null ? `${csat.csat_top_box_pct}%` : "—"}
        accent="ok"
      />

      <h3 className="text-sm font-semibold text-slate-700 mt-3">SLA</h3>
      <Stat label="Total convos" value={sla?.total_conversations ?? "—"} />
      <Stat
        label="First-response breaches"
        value={sla?.first_response_breaches ?? 0}
        accent={(sla?.first_response_breaches ?? 0) > 0 ? "error" : "ok"}
      />
      <Stat
        label="Resolution breaches"
        value={sla?.resolution_breaches ?? 0}
        accent={(sla?.resolution_breaches ?? 0) > 0 ? "error" : "ok"}
      />
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: "ok" | "warn" | "error";
}) {
  const cls =
    accent === "ok"
      ? "text-emerald-700"
      : accent === "warn"
      ? "text-amber-700"
      : accent === "error"
      ? "text-rose-700"
      : "text-slate-800";
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 pb-2 last:border-0">
      <span className="text-xs text-slate-500">{label}</span>
      <span className={`font-semibold tabular-nums ${cls}`}>{value}</span>
    </div>
  );
}
