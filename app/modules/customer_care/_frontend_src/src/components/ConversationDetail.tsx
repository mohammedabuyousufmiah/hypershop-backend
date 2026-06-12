import clsx from "clsx";
import { format } from "date-fns";
import { useEffect, useRef } from "react";
import useSWR from "swr";
import { swrFetcher } from "../api/client";
import { conversations as convoApi } from "../api/endpoints";
import type { Conversation, Message } from "../types";
import { MessageComposer } from "./MessageComposer";
import { SlaBadge } from "./SlaBadge";

interface Props {
  conversationId: string | null;
}

export function ConversationDetail({ conversationId }: Props) {
  const { data: convo, mutate: mutateConvo } = useSWR<Conversation>(
    conversationId ? `/api/conversations/${conversationId}` : null,
    swrFetcher,
    { refreshInterval: 0 },
  );
  const { data: messages, mutate: mutateMsgs } = useSWR<Message[]>(
    conversationId ? `/api/conversations/${conversationId}/messages` : null,
    swrFetcher,
    { refreshInterval: 5000 }, // periodic refresh as a safety net to SSE
  );

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, conversationId]);

  if (!conversationId) {
    return (
      <div className="flex-1 grid place-items-center text-slate-400 text-sm">
        Select a conversation to view messages
      </div>
    );
  }

  const handleResolve = async () => {
    if (!convo) return;
    if (!confirm("Mark conversation as resolved? This will trigger a CSAT survey.")) return;
    await convoApi.resolve(convo.id);
    await mutateConvo();
  };

  const handleHandover = async () => {
    if (!convo) return;
    await convoApi.handover(convo.id);
    await mutateConvo();
  };

  const handleSend = async (text: string) => {
    if (!conversationId || !text.trim()) return;
    await convoApi.send(conversationId, text);
    await mutateMsgs();
  };

  return (
    <div className="flex flex-col h-full bg-slate-50">
      <header className="card rounded-none border-x-0 border-t-0 px-4 py-3 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="font-semibold text-sm">
              {convo ? `Conversation ${convo.id.slice(0, 8)}…` : "Loading…"}
            </h2>
            {convo && <SlaBadge conversation={convo} />}
            {convo?.handover_required && (
              <span className="badge-info">Handover: {convo.handover_reason}</span>
            )}
          </div>
          {convo && (
            <p className="text-xs text-slate-500 mt-0.5">
              {convo.channel} · {convo.status} ·{" "}
              {convo.first_response_at ? "responded" : "awaiting response"}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={handleHandover} className="btn-ghost">
            Handover
          </button>
          <button
            onClick={handleResolve}
            className="btn-primary"
            disabled={!convo || convo.status === "resolved"}
          >
            Resolve
          </button>
        </div>
      </header>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-2">
        {messages?.length === 0 && (
          <p className="text-center text-sm text-slate-400 mt-12">No messages yet.</p>
        )}
        {messages?.map((m) => (
          <MessageBubble key={m.id} m={m} />
        ))}
      </div>

      <MessageComposer
        disabled={!convo || convo.status === "resolved"}
        onSend={handleSend}
      />
    </div>
  );
}

function MessageBubble({ m }: { m: Message }) {
  const isCustomer = m.sender_type === "customer";
  const isAi = m.sender_type === "ai";
  return (
    <div
      className={clsx(
        "max-w-[75%] rounded-xl px-3 py-2 text-sm whitespace-pre-wrap break-words",
        isCustomer && "bg-white border border-slate-200 self-start mr-auto",
        isAi && "bg-brand-50 border border-brand-100 self-start mr-auto",
        !isCustomer && !isAi && "bg-brand-500 text-white self-end ml-auto",
      )}
      lang="bn"
    >
      {m.message_body || <span className="italic">(empty)</span>}
      <div
        className={clsx(
          "text-[10px] mt-1 flex items-center gap-1 opacity-70",
          !isCustomer && !isAi ? "text-brand-100" : "text-slate-500",
        )}
      >
        <span className="uppercase">{m.sender_type}</span>
        {m.ai_confidence != null && <span>· {(Number(m.ai_confidence) * 100).toFixed(0)}%</span>}
        <span>· {format(new Date(m.created_at), "HH:mm")}</span>
      </div>
    </div>
  );
}
