import { useCallback, useState } from "react";
import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { ConversationDetail } from "../components/ConversationDetail";
import { ConversationList } from "../components/ConversationList";
import { ReportsPanel } from "../components/ReportsPanel";
import { useEventStream } from "../hooks/useEventStream";
import { useLocale } from "../i18n";
import type { Conversation, InboxEvent } from "../types";

export default function InboxPage() {
  const { user, logout } = useAuth();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const { data, mutate } = useSWR<Conversation[]>(
    "/api/v1/customer-care/conversations?scope=mine",
    swrFetcher,
    { refreshInterval: 15_000 },
  );

  const onEvent = useCallback(
    (_e: InboxEvent) => {
      // Any inbox event = revalidate the conversation list, and if it's
      // about the selected conversation, refresh its messages too.
      void mutate();
    },
    [mutate],
  );

  const { status: sseStatus, lastEvent } = useEventStream({ onEvent });
  const { locale, setLocale, t } = useLocale();

  return (
    <div className="grid grid-cols-12 h-full">
      <header className="col-span-12 bg-white border-b border-slate-200 px-4 py-2 flex items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <div className="h-8 w-8 rounded bg-brand-500 text-white grid place-items-center text-sm font-bold">
            CC
          </div>
          <div>
            <h1 className="text-sm font-semibold leading-tight">{t("app.title")}</h1>
            <p className="text-[11px] text-slate-500 leading-tight">
              {t("header.signed_in_as")}: {user?.email ?? user?.username ?? "—"} · {user?.role ?? "agent"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <SseIndicator status={sseStatus} />
          {lastEvent && (
            <span className="text-slate-500 italic max-w-xs truncate">
              last: {lastEvent.type}
            </span>
          )}
          <select
            value={locale}
            onChange={(e) => setLocale(e.target.value as "en" | "bn")}
            className="border border-slate-300 rounded px-2 py-1 text-xs bg-white"
            aria-label="Language"
          >
            <option value="en">English</option>
            <option value="bn">বাংলা</option>
          </select>
          <button onClick={() => void logout()} className="btn-ghost">
            {t("header.logout")}
          </button>
        </div>
      </header>

      <aside className="col-span-3 row-start-2 h-[calc(100vh-49px)]">
        <ConversationList
          conversations={data ?? []}
          selectedId={selectedId}
          onSelect={setSelectedId}
          filter={filter}
          onFilterChange={setFilter}
        />
      </aside>

      <main className="col-span-6 row-start-2 h-[calc(100vh-49px)]">
        <ConversationDetail conversationId={selectedId} />
      </main>

      <aside className="col-span-3 row-start-2 h-[calc(100vh-49px)]">
        <ReportsPanel />
      </aside>
    </div>
  );
}

function SseIndicator({ status }: { status: "connecting" | "open" | "closed" | "error" }) {
  const colour =
    status === "open"
      ? "bg-emerald-500"
      : status === "connecting"
      ? "bg-amber-500"
      : "bg-rose-500";
  return (
    <span className="inline-flex items-center gap-1.5 text-slate-600">
      <span className={`h-2 w-2 rounded-full pulse-dot ${colour}`} />
      {status}
    </span>
  );
}
