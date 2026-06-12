import clsx from "clsx";
import { formatDistanceToNow } from "date-fns";
import type { Conversation } from "../types";
import { SlaBadge } from "./SlaBadge";

interface Props {
  conversations: Conversation[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  filter: string;
  onFilterChange: (q: string) => void;
}

export function ConversationList({
  conversations,
  selectedId,
  onSelect,
  filter,
  onFilterChange,
}: Props) {
  const filtered = filter
    ? conversations.filter((c) =>
        (c.last_message ?? "").toLowerCase().includes(filter.toLowerCase()),
      )
    : conversations;

  return (
    <div className="flex flex-col h-full bg-white border-r border-slate-200">
      <div className="p-3 border-b border-slate-200">
        <input
          className="input"
          placeholder="Search conversations…"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
        />
      </div>
      <div className="flex-1 overflow-y-auto">
        {filtered.length === 0 && (
          <div className="p-6 text-center text-sm text-slate-500">
            {filter ? "No matches" : "No conversations yet"}
          </div>
        )}
        {filtered.map((c) => {
          const isOpen = c.status === "open" || c.status === "pending";
          const ago = c.last_message_at
            ? formatDistanceToNow(new Date(c.last_message_at), { addSuffix: true })
            : "";
          return (
            <button
              key={c.id}
              onClick={() => onSelect(c.id)}
              className={clsx(
                "w-full text-left px-3 py-3 border-b border-slate-100 hover:bg-slate-50 transition flex flex-col gap-1",
                selectedId === c.id && "bg-brand-50 hover:bg-brand-50 border-l-4 border-l-brand-500",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium text-sm truncate">{c.customer_id.slice(0, 8)}</span>
                <span className="text-[11px] text-slate-400 shrink-0">{ago}</span>
              </div>
              <p className="text-xs text-slate-600 truncate" lang="bn">
                {c.last_message || <span className="italic text-slate-400">(no message)</span>}
              </p>
              <div className="flex items-center gap-1 flex-wrap">
                <span
                  className={clsx(
                    "badge",
                    isOpen ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600",
                  )}
                >
                  {c.status}
                </span>
                {c.priority === "high" && <span className="badge-warn">high</span>}
                {c.handover_required && <span className="badge-info">handover</span>}
                {c.channel !== "whatsapp" && (
                  <span className="badge bg-purple-50 text-purple-700">{c.channel}</span>
                )}
                <SlaBadge conversation={c} />
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
