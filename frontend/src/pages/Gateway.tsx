import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  addCredential,
  getGatewayConfig,
  getGatewayStatus,
  listCredentials,
  listInboxPaths,
  listSessions,
  removeCredential,
  startGateway,
  stopGateway,
  updateCredential,
  type CredentialCreate,
} from '../api/gateway';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes, formatDate } from '../lib/utils';

function StatusBadge({ status }: { status: string }) {
  const variant =
    status === 'running' ? 'green' : status === 'stopped' ? 'red' : ('amber' as const);
  return <Badge variant={variant}>{status}</Badge>;
}

function AddCredentialForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const { data: inboxPaths } = useQuery({ queryKey: ['gateway', 'inbox-paths'], queryFn: listInboxPaths });
  const [form, setForm] = useState<CredentialCreate>({ username: '', password: '', allowed_paths: [] });
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: addCredential,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['gateway', 'credentials'] });
      onDone();
    },
    onError: (err: Error) => setError(err.message),
  });

  const togglePath = (path: string) => {
    setForm(f => ({
      ...f,
      allowed_paths: f.allowed_paths?.includes(path)
        ? f.allowed_paths.filter(p => p !== path)
        : [...(f.allowed_paths ?? []), path],
    }));
  };

  return (
    <Card className="border-blue-500/30 bg-blue-500/5">
      <h3 className="mb-4 font-semibold text-slate-100">Add SFTP Credential</h3>
      <div className="grid gap-3">
        <div>
          <label className="mb-1 block text-sm text-slate-400">Username</label>
          <input
            className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-1.5 text-slate-100 text-sm focus:border-blue-500 focus:outline-none"
            value={form.username}
            onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
            placeholder="e.g. archive-user"
          />
        </div>
        <div>
          <label className="mb-1 block text-sm text-slate-400">Password</label>
          <input
            type="password"
            className="w-full rounded border border-slate-600 bg-slate-800 px-3 py-1.5 text-slate-100 text-sm focus:border-blue-500 focus:outline-none"
            value={form.password}
            onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
            placeholder="Strong password"
          />
        </div>
        <div>
          <label className="mb-2 block text-sm text-slate-400">Allowed Inbox Paths</label>
          <div className="flex flex-wrap gap-2">
            {(inboxPaths ?? []).map(opt => (
              <button
                key={opt.path}
                type="button"
                onClick={() => togglePath(opt.path)}
                className={`rounded px-3 py-1 text-xs font-mono border transition-colors ${
                  form.allowed_paths?.includes(opt.path)
                    ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                    : 'border-slate-600 bg-slate-800 text-slate-400 hover:border-slate-400'
                }`}
              >
                {opt.path}
              </button>
            ))}
          </div>
          <p className="mt-1 text-xs text-slate-500">Leave empty to allow all paths.</p>
        </div>
        {error && <p className="text-sm text-red-400">{error}</p>}
        <div className="flex gap-2">
          <button
            onClick={() => mutation.mutate(form)}
            disabled={!form.username || !form.password || mutation.isPending}
            className="rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {mutation.isPending ? 'Adding…' : 'Add Credential'}
          </button>
          <button
            onClick={onDone}
            className="rounded border border-slate-600 px-4 py-1.5 text-sm text-slate-300 hover:bg-slate-700"
          >
            Cancel
          </button>
        </div>
      </div>
    </Card>
  );
}

export default function GatewayPage() {
  const qc = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [activeOnly, setActiveOnly] = useState(false);

  const configQuery = useQuery({ queryKey: ['gateway', 'config'], queryFn: getGatewayConfig });
  const statusQuery = useQuery({ queryKey: ['gateway', 'status'], queryFn: getGatewayStatus, refetchInterval: 10_000 });
  const credsQuery = useQuery({ queryKey: ['gateway', 'credentials'], queryFn: listCredentials });
  const sessionsQuery = useQuery({
    queryKey: ['gateway', 'sessions', activeOnly],
    queryFn: () => listSessions(activeOnly),
    refetchInterval: 10_000,
  });

  const startMut = useMutation({ mutationFn: startGateway, onSuccess: () => void qc.invalidateQueries({ queryKey: ['gateway'] }) });
  const stopMut = useMutation({ mutationFn: stopGateway, onSuccess: () => void qc.invalidateQueries({ queryKey: ['gateway'] }) });
  const removeMut = useMutation({
    mutationFn: removeCredential,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['gateway', 'credentials'] }),
  });
  const toggleMut = useMutation({
    mutationFn: ({ username, enabled }: { username: string; enabled: boolean }) =>
      updateCredential(username, { enabled }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['gateway', 'credentials'] }),
  });

  const isLoading = configQuery.isLoading || statusQuery.isLoading;
  if (isLoading) return <Spinner />;
  if (configQuery.isError) return <ErrorMessage error={configQuery.error} onRetry={() => void configQuery.refetch()} />;

  const config = configQuery.data;
  const stats = statusQuery.data;
  const creds = credsQuery.data ?? [];
  const sessions = sessionsQuery.data ?? [];
  const isRunning = stats?.status === 'running';

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">Protocol Gateway</h1>
          <p className="text-sm text-slate-400">SFTP/SCP gateway for secure inbox uploads</p>
        </div>
        <div className="flex items-center gap-3">
          {stats && <StatusBadge status={stats.status} />}
          {isRunning ? (
            <button
              onClick={() => stopMut.mutate()}
              disabled={stopMut.isPending}
              className="rounded border border-red-500/50 bg-red-500/10 px-4 py-1.5 text-sm text-red-300 hover:bg-red-500/20 disabled:opacity-50"
            >
              {stopMut.isPending ? 'Stopping…' : 'Stop Gateway'}
            </button>
          ) : (
            <button
              onClick={() => startMut.mutate()}
              disabled={startMut.isPending}
              className="rounded bg-emerald-600 px-4 py-1.5 text-sm text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {startMut.isPending ? 'Starting…' : 'Start Gateway'}
            </button>
          )}
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {[
          { label: 'Status', value: stats?.status ?? '—' },
          { label: 'Active Sessions', value: stats?.active_sessions ?? 0 },
          { label: 'Total Uploaded', value: formatBytes(stats?.total_bytes_uploaded ?? 0) },
          { label: 'Files Uploaded', value: stats?.total_files_uploaded ?? 0 },
        ].map(({ label, value }) => (
          <Card key={label}>
            <div className="text-xs text-slate-400">{label}</div>
            <div className="mt-1 text-xl font-semibold text-slate-100">{value}</div>
          </Card>
        ))}
      </div>

      {/* Config Info */}
      {config && (
        <Card>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Connection Details</h2>
          <div className="grid grid-cols-2 gap-3 text-sm lg:grid-cols-4">
            <div>
              <div className="text-slate-500">Host</div>
              <div className="font-mono text-slate-200">{config.bind_host}</div>
            </div>
            <div>
              <div className="text-slate-500">Port</div>
              <div className="font-mono text-slate-200">{config.bind_port}</div>
            </div>
            <div>
              <div className="text-slate-500">Max Sessions</div>
              <div className="font-mono text-slate-200">{config.max_sessions}</div>
            </div>
            <div>
              <div className="text-slate-500">Inbox Root</div>
              <div className="font-mono text-slate-200 truncate">{config.inbox_root}</div>
            </div>
          </div>
        </Card>
      )}

      {/* Credentials */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">SFTP Credentials</h2>
          <button
            onClick={() => setShowAdd(v => !v)}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
          >
            {showAdd ? 'Cancel' : '+ Add Credential'}
          </button>
        </div>

        {showAdd && <div className="mb-4"><AddCredentialForm onDone={() => setShowAdd(false)} /></div>}

        {credsQuery.isLoading ? (
          <Spinner />
        ) : creds.length === 0 ? (
          <Card className="text-center text-slate-400">No credentials configured.</Card>
        ) : (
          <Card className="overflow-hidden p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800/50">
                  <th className="px-4 py-2 text-left text-slate-400">Username</th>
                  <th className="px-4 py-2 text-left text-slate-400">Status</th>
                  <th className="px-4 py-2 text-left text-slate-400">Allowed Paths</th>
                  <th className="px-4 py-2 text-right text-slate-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {creds.map(cred => (
                  <tr key={cred.username} className="border-b border-slate-700/50 hover:bg-slate-800/30">
                    <td className="px-4 py-3 font-mono text-slate-200">{cred.username}</td>
                    <td className="px-4 py-3">
                      <Badge variant={cred.enabled ? 'green' : 'red'}>
                        {cred.enabled ? 'enabled' : 'disabled'}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {(cred.allowed_paths.length > 0 ? cred.allowed_paths : ['all']).map(p => (
                          <span key={p} className="rounded bg-slate-700 px-2 py-0.5 text-xs font-mono text-slate-300">{p}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-2">
                        <button
                          onClick={() => toggleMut.mutate({ username: cred.username, enabled: !cred.enabled })}
                          className="rounded border border-slate-600 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-700"
                        >
                          {cred.enabled ? 'Disable' : 'Enable'}
                        </button>
                        <button
                          onClick={() => { if (confirm(`Remove credential "${cred.username}"?`)) removeMut.mutate(cred.username); }}
                          className="rounded border border-red-500/40 px-2 py-0.5 text-xs text-red-400 hover:bg-red-500/10"
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>

      {/* Session Audit */}
      <div>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-semibold text-slate-100">Session Audit</h2>
          <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={e => setActiveOnly(e.target.checked)}
              className="rounded border-slate-600"
            />
            Active only
          </label>
        </div>

        {sessionsQuery.isLoading ? (
          <Spinner />
        ) : sessions.length === 0 ? (
          <Card className="text-center text-slate-400">No sessions recorded.</Card>
        ) : (
          <Card className="overflow-hidden p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700 bg-slate-800/50">
                  <th className="px-4 py-2 text-left text-slate-400">User</th>
                  <th className="px-4 py-2 text-left text-slate-400">Remote</th>
                  <th className="px-4 py-2 text-left text-slate-400">Connected</th>
                  <th className="px-4 py-2 text-left text-slate-400">Disconnected</th>
                  <th className="px-4 py-2 text-right text-slate-400">Files</th>
                  <th className="px-4 py-2 text-right text-slate-400">Bytes</th>
                  <th className="px-4 py-2 text-right text-slate-400">Errors</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map(s => (
                  <tr key={s.session_id} className="border-b border-slate-700/50 hover:bg-slate-800/30">
                    <td className="px-4 py-3 font-mono text-slate-200">{s.username}</td>
                    <td className="px-4 py-3 font-mono text-slate-400 text-xs">{s.remote_addr}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{formatDate(s.connected_at)}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {s.disconnected_at ? formatDate(s.disconnected_at) : <Badge variant="green">active</Badge>}
                    </td>
                    <td className="px-4 py-3 text-right text-slate-300">{s.files_uploaded}</td>
                    <td className="px-4 py-3 text-right text-slate-300">{formatBytes(s.bytes_uploaded)}</td>
                    <td className="px-4 py-3 text-right">
                      {s.errors > 0 ? (
                        <Badge variant="red">{s.errors}</Badge>
                      ) : (
                        <span className="text-slate-500">0</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>
    </div>
  );
}
