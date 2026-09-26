import { useState, type FormEvent } from 'react';
import { useMutation } from '@tanstack/react-query';
import { askAssistant, ASSIST_MAX_MESSAGE_CHARS, type AssistMessage } from '../api/assist';
import { ApiError } from '../api/client';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

interface Turn extends AssistMessage {
  /** Tool names the assistant consulted for this reply. Names only, never args. */
  toolCalls?: string[];
}

const SUGGESTIONS = [
  'How many tapes are in the library, and which are scratch?',
  'Why did my last restore fail?',
  'Which cartridges hold data for the photo-archive volume group?',
  'Show me the drives and what is loaded in them.',
];

function ToolTrace({ toolCalls }: { toolCalls: string[] }) {
  return (
    <p className="mt-2 font-mono text-xs text-slate-600">
      consulted {toolCalls.join(', ')}
    </p>
  );
}

function SetupInstructions({ detail }: { detail: string }) {
  return (
    <Card>
      <h2 className="text-lg font-semibold text-slate-100">The assistant is not configured</h2>
      <p className="mt-2 text-sm text-slate-400">
        The API answered 503: no model endpoint is set, so there is nothing to ask. Point OpenBlade
        at an Ollama endpoint and restart it:
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md border border-quantum-border bg-quantum-panel p-4 font-mono text-xs text-slate-300">
        {'# local\nexport OPENBLADE_OLLAMA_URL=http://localhost:11434\n\n'}
        {'# cloud\nexport OPENBLADE_OLLAMA_URL=https://ollama.com\nexport OPENBLADE_OLLAMA_API_KEY=<your key>'}
      </pre>
      <p className="mt-3 text-xs text-slate-500">Reported by the backend: {detail}</p>
    </Card>
  );
}

export default function Assistant() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');

  const askMutation = useMutation({
    mutationFn: (messages: AssistMessage[]) =>
      askAssistant(messages.map(({ role, content }) => ({ role, content }))),
    onSuccess: (reply) => {
      setTurns((current) => [
        ...current,
        { role: 'assistant', content: reply.reply, toolCalls: reply.toolCalls },
      ]);
    },
    onError: (_error, variables) => {
      // Nothing answered the question, whatever the reason (not configured, rate
      // limited, provider down), so the thread must not keep a turn that looks
      // answered: pop it and hand the text back to the composer to retry.
      setTurns((current) => current.slice(0, -1));
      setDraft(variables[variables.length - 1]?.content ?? '');
    },
  });

  function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || askMutation.isPending) {
      return;
    }
    const next: Turn[] = [...turns, { role: 'user', content: trimmed }];
    setTurns(next);
    setDraft('');
    askMutation.mutate(next);
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    send(draft);
  }

  const error = askMutation.error;
  const notConfigured = error instanceof ApiError && error.status === 503;

  return (
    <div className="space-y-4">
      <Card>
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-[0.22em] text-red-300/70">Assistant</p>
          <h1 className="text-2xl font-semibold text-white">Assistant</h1>
          <p className="max-w-3xl text-sm text-slate-400">
            Ask questions about this library, its catalog and its jobs. The HTTP assistant is
            read-only by construction: it can look at everything and change nothing, so it will
            never offer to create, format or erase anything.
          </p>
        </div>
      </Card>

      <Card>
        {turns.length === 0 ? (
          <div className="space-y-3">
            <h2 className="text-lg font-semibold text-slate-100">Ask about the library</h2>
            <p className="text-sm text-slate-400">
              Every answer is read live from the catalog and the library, not from a cached summary.
            </p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => send(suggestion)}
                  className="rounded-full border border-quantum-border bg-quantum-panel px-3 py-1.5 text-xs text-slate-300 transition hover:bg-quantum-north hover:text-white"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="space-y-4">
            {turns.map((turn, index) => (
              <li
                key={`${turn.role}-${index}`}
                className={
                  turn.role === 'user'
                    ? 'rounded-md border border-quantum-border bg-quantum-panel p-4'
                    : 'rounded-md border border-quantum-red/30 bg-quantum-north/40 p-4'
                }
              >
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
                  {turn.role === 'user' ? 'You' : 'Assistant'}
                </div>
                <p className="mt-2 whitespace-pre-wrap text-sm text-slate-200">{turn.content}</p>
                {turn.toolCalls && turn.toolCalls.length > 0 ? (
                  <ToolTrace toolCalls={turn.toolCalls} />
                ) : null}
              </li>
            ))}
          </ol>
        )}

        {askMutation.isPending ? (
          <div className="mt-4 flex items-center gap-3 text-sm text-slate-400">
            <Spinner />
            Thinking — a turn can take a while, and there is no partial answer to show.
          </div>
        ) : null}

        <form className="mt-4 flex flex-col gap-3 sm:flex-row" onSubmit={onSubmit}>
          <label className="sr-only" htmlFor="assistant-question">
            Question
          </label>
          <input
            id="assistant-question"
            value={draft}
            maxLength={ASSIST_MAX_MESSAGE_CHARS}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Ask about tapes, jobs, drives or the catalog…"
            autoComplete="off"
            className="flex-1 rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
          />
          <Button type="submit" disabled={!draft.trim() || askMutation.isPending}>
            {askMutation.isPending ? 'Asking…' : 'Ask'}
          </Button>
        </form>
      </Card>

      {notConfigured ? (
        <SetupInstructions detail={error.message} />
      ) : askMutation.isError ? (
        <ErrorMessage error={error} />
      ) : null}
    </div>
  );
}
