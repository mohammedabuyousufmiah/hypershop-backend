import clsx from "clsx";
import type { Conversation } from "../types";

interface Props {
  conversation: Conversation;
  className?: string;
}

export function SlaBadge({ conversation, className }: Props) {
  const breached =
    conversation.sla_first_response_breached || conversation.sla_resolution_breached;
  if (breached) {
    return (
      <span className={clsx("badge-error", className)}>
        SLA breached
      </span>
    );
  }
  const dueAt = conversation.first_response_at
    ? conversation.sla_resolution_due_at
    : conversation.sla_first_response_due_at;
  if (!dueAt) return null;

  const ms = new Date(dueAt).getTime() - Date.now();
  const mins = Math.round(ms / 60_000);
  if (mins <= 0) {
    return <span className={clsx("badge-error", className)}>Overdue</span>;
  }
  if (mins <= 5) {
    return <span className={clsx("badge-warn", className)}>{mins}m left</span>;
  }
  return <span className={clsx("badge-ok", className)}>{mins}m</span>;
}
