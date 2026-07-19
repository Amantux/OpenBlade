import { useEffect, useRef, useState } from 'react';
import {
  getAssistantStatus,
  sendAssistantChat,
  type AssistantChatMessage,
  type AssistantStatus,
} from '../api/assistant';

interface ChatEntry extends AssistantChatMessage {
  toolsUsed?: string[];
}

export default function Assistant() {
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getAssistantStatus().then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [entries, sending]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setError(null);
    const history: ChatEntry[] = [...entries, { role: 'user', content: text }];
    setEntries(history);
    setInput('');
    setSending(true);
    try {
      const payload: AssistantChatMessage[] = history.map(({ role, content }) => ({ role, content }));
      const response = await sendAssistantChat(payload);
      setEntries((prev) => [
        ...prev,
        { role: 'assistant', content: response.reply, toolsUsed: response.tools_used },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'The assistant request failed.');
    } finally {
      setSending(false);
    }
  }

  const disabled = status !== null && (!status.enabled || !status.configured);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-4 p-4">
      <div>
        <h1 className="text-2xl font-semibold">Assistant</h1>
        <p className="text-sm text-gray-500">
          Ask about library state, archive/restore, NAS config, diagnostics, and security posture.
          Advisory only — it never performs or bypasses safety-gated operations.
        </p>
      </div>

      {disabled && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">AI assistant is not configured.</p>
          <p className="mt-1">
            Point OpenBlade at an OpenAI-compatible endpoint by setting{' '}
            <code>OPENBLADE_ASSISTANT_BASE_URL</code>, <code>OPENBLADE_ASSISTANT_MODEL</code>, and{' '}
            <code>OPENBLADE_ASSISTANT_API_KEY</code> (or the standard <code>OPENAI_*</code> variables),
            then restart the API. Works with OpenAI, Ollama, vLLM, LM Studio, and similar.
          </p>
        </div>
      )}

      <div
        ref={scrollRef}
        className="flex-1 space-y-3 overflow-y-auto rounded-md border border-gray-200 bg-white p-4"
      >
        {entries.length === 0 && !disabled && (
          <p className="text-sm text-gray-400">
            Try: “Summarize library health and any safety risks”, or “Which drives are loaded?”
          </p>
        )}
        {entries.map((entry, index) => (
          <div
            key={index}
            className={entry.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
          >
            <div
              className={
                entry.role === 'user'
                  ? 'max-w-[85%] whitespace-pre-wrap rounded-lg bg-blue-600 px-3 py-2 text-sm text-white'
                  : 'max-w-[85%] whitespace-pre-wrap rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-900'
              }
            >
              {entry.content}
              {entry.toolsUsed && entry.toolsUsed.length > 0 && (
                <div className="mt-1 text-xs text-gray-500">
                  inspected: {entry.toolsUsed.join(', ')}
                </div>
              )}
            </div>
          </div>
        ))}
        {sending && <p className="text-sm text-gray-400">Thinking…</p>}
      </div>

      {error && <p className="text-sm text-red-600">{error}</p>}

      <div className="flex gap-2">
        <textarea
          className="min-h-[44px] flex-1 resize-y rounded-md border border-gray-300 px-3 py-2 text-sm"
          placeholder={disabled ? 'Configure an endpoint to chat…' : 'Ask the assistant…'}
          value={input}
          disabled={disabled || sending}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              void handleSend();
            }
          }}
        />
        <button
          type="button"
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          disabled={disabled || sending || input.trim().length === 0}
          onClick={() => void handleSend()}
        >
          Send
        </button>
      </div>
    </div>
  );
}
