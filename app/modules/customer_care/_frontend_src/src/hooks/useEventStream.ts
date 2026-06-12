import { useEffect, useRef, useState } from "react";
import { tokenStore } from "../api/client";
import type { InboxEvent } from "../types";

type Status = "connecting" | "open" | "closed" | "error";

interface UseEventStreamOpts {
  onEvent?: (e: InboxEvent) => void;
  enabled?: boolean;
}

/**
 * SSE wrapper for `/api/inbox/stream`. EventSource doesn't allow custom headers
 * (notably Authorization), so we pass the token as a query string. The backend
 * accepts that fallback for SSE only.
 *
 * Auto-reconnects with exponential backoff capped at 30s.
 */
export function useEventStream({ onEvent, enabled = true }: UseEventStreamOpts) {
  const [status, setStatus] = useState<Status>("connecting");
  const [lastEvent, setLastEvent] = useState<InboxEvent | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const retryRef = useRef<number>(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | undefined;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      const access = tokenStore.access();
      if (!access) {
        setStatus("closed");
        return;
      }
      // EventSource has no header support → send token via query.
      // The backend should accept ?token=... as an alternate auth path on
      // the SSE route only. If not, the connection 401s and we close.
      const url = `/api/inbox/stream?token=${encodeURIComponent(access)}`;
      const src = new EventSource(url, { withCredentials: false });
      sourceRef.current = src;

      src.onopen = () => {
        if (cancelled) return;
        retryRef.current = 0;
        setStatus("open");
      };

      src.onmessage = (msg) => {
        if (cancelled) return;
        try {
          const data = JSON.parse(msg.data) as InboxEvent;
          setLastEvent(data);
          onEvent?.(data);
        } catch {
          /* ignore malformed event */
        }
      };

      src.onerror = () => {
        if (cancelled) return;
        setStatus("error");
        src.close();
        // Exponential backoff, max 30s
        const delay = Math.min(30_000, 1_000 * 2 ** retryRef.current);
        retryRef.current += 1;
        timer = window.setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
      sourceRef.current?.close();
      setStatus("closed");
    };
  }, [enabled, onEvent]);

  return { status, lastEvent };
}
