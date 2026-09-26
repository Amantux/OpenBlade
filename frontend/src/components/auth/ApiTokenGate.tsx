import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import Button from '../ui/Button';
import Card from '../ui/Card';
import {
  API_TOKEN_REQUIRED_EVENT,
  reloadForApiToken,
  setApiToken,
  type ApiTokenPromptReason,
} from '../../lib/apiToken';

const MESSAGES: Record<ApiTokenPromptReason, string> = {
  missing:
    'This appliance requires an API token. Paste the token from OPENBLADE_API_TOKEN (or the file named by OPENBLADE_API_TOKEN_FILE) to continue.',
  rejected:
    'The stored API token was rejected and has been discarded. Paste the token currently configured on this appliance to continue.',
};

interface ApiTokenGateProps {
  /** Injected in tests; production replays the current URL with the new token. */
  onAccepted?: () => void;
}

/**
 * The token-entry surface for the native REST API.
 *
 * It renders nothing until the API client reports a 401 from the bearer-token
 * gate, so a deployment with `OPENBLADE_API_TOKEN` unset never sees it: no login
 * wall, no extra request, no change in behaviour. On submit the token is stored
 * and the app reloads, which re-runs the navigation that failed.
 *
 * The token is write-only here — it is masked on input and never echoed back,
 * logged, or included in any message.
 */
export default function ApiTokenGate({ onAccepted = reloadForApiToken }: ApiTokenGateProps) {
  const [reason, setReason] = useState<ApiTokenPromptReason | null>(null);
  const [token, setToken] = useState('');
  const [remember, setRemember] = useState(false);

  useEffect(() => {
    function handleTokenRequired(event: Event) {
      const detail = (event as CustomEvent<ApiTokenPromptReason>).detail;
      setReason(detail === 'rejected' ? 'rejected' : 'missing');
    }

    window.addEventListener(API_TOKEN_REQUIRED_EVENT, handleTokenRequired);
    return () => window.removeEventListener(API_TOKEN_REQUIRED_EVENT, handleTokenRequired);
  }, []);

  if (!reason) {
    return null;
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token.trim()) {
      return;
    }

    setApiToken(token, remember);
    setToken('');
    setReason(null);
    onAccepted();
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="api-token-gate-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 px-4 py-10 text-slate-100"
    >
      <Card className="w-full max-w-lg bg-quantum-info p-8">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">API authentication</div>
        <h2 id="api-token-gate-title" className="mt-1 text-2xl font-semibold text-slate-100">
          API token required
        </h2>
        <p className="mt-3 text-sm text-slate-400">{MESSAGES[reason]}</p>

        <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
          <div>
            <label
              className="mb-2 block text-xs uppercase tracking-[0.18em] text-slate-500"
              htmlFor="api-token-gate-input"
            >
              API token
            </label>
            <input
              id="api-token-gate-input"
              className="w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              required
            />
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-400">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
            />
            Remember on this device
          </label>
          <p className="text-xs text-slate-500">
            Left unchecked, the token is kept for this browser tab only and forgotten when it closes.
          </p>

          <Button type="submit" className="w-full">
            Use this token
          </Button>
        </form>
      </Card>
    </div>
  );
}
