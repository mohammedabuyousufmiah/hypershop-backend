import { useState } from "react";

interface Props {
  disabled?: boolean;
  onSend: (text: string) => Promise<void> | void;
}

export function MessageComposer({ disabled, onSend }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);

  const submit = async () => {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      await onSend(text);
      setText("");
    } finally {
      setSending(false);
    }
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit();
    }
  };

  return (
    <div className="border-t border-slate-200 bg-white p-3 flex items-end gap-2">
      <textarea
        rows={2}
        className="input resize-none flex-1"
        placeholder={
          disabled
            ? "Conversation is resolved — reopen to reply"
            : "Type a reply…  Shift+Enter for new line"
        }
        disabled={disabled}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        lang="bn"
      />
      <button
        className="btn-primary py-2.5 px-4 self-stretch"
        onClick={() => void submit()}
        disabled={disabled || sending || !text.trim()}
      >
        {sending ? "Sending…" : "Send"}
      </button>
    </div>
  );
}
