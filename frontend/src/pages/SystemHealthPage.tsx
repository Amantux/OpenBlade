import { useQuery } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getSystemHealthDashboard } from '../api/catalogAdmin';
import { formatDate, toTitleCase } from '../lib/utils';

function statusVariant(status: string): 'green' | 'amber' | 'red' | 'gray' {
  switch (status.toLowerCase()) {
    case 'ok':
    case 'healthy':
      return 'green';
    case 'degraded':
      return 'amber';
    case 'unhealthy':
      return 'red';
    default:
      return 'gray';
  }
}

export default function SystemHealthPage() {
  const healthQuery = useQuery({
    queryKey: ['system-health-dashboard'],
    queryFn: getSystemHealthDashboard,
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">System health</h1>
            <p className="mt-2 text-sm text-slate-400">Live public health, readiness, and version telemetry with 30-second auto-refresh.</p>
          </div>
          <Button variant="secondary" onClick={() => void healthQuery.refetch()}>
            Refresh
          </Button>
        </div>
      </Card>

      {healthQuery.isLoading ? <Spinner /> : null}
      {healthQuery.isError ? <ErrorMessage error={healthQuery.error} onRetry={() => healthQuery.refetch()} /> : null}
      {healthQuery.data ? (
        <>
          <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
            <Card>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-white">Overall health</h2>
                  <p className="mt-1 text-sm text-slate-400">Last checked {formatDate(healthQuery.data.health.checked_at)}</p>
                </div>
                <Badge variant={statusVariant(healthQuery.data.health.status)}>{healthQuery.data.health.status.toUpperCase()}</Badge>
              </div>
            </Card>

            <Card>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-white">Readiness</h2>
                  <p className="mt-1 text-sm text-slate-400">Checked {formatDate(healthQuery.data.readiness.checked_at)}</p>
                </div>
                <Badge variant={healthQuery.data.readiness.ready ? 'green' : 'red'}>
                  {healthQuery.data.readiness.ready ? 'READY' : 'NOT READY'}
                </Badge>
              </div>
              <div className="mt-3 text-sm text-slate-300">{healthQuery.data.readiness.reason || 'All readiness checks passed.'}</div>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.2fr,0.8fr]">
            <Card>
              <h2 className="text-lg font-semibold text-white">Components</h2>
              {healthQuery.data.health.components.length === 0 ? (
                <div className="mt-4 rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">
                  No component checks reported.
                </div>
              ) : (
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  {healthQuery.data.health.components.map((component) => (
                    <div key={component.name} className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-semibold text-white">{toTitleCase(component.name)}</div>
                        <Badge variant={statusVariant(component.status)}>{component.status.toUpperCase()}</Badge>
                      </div>
                      <div className="mt-3 text-sm text-slate-300">{component.message || 'No message provided.'}</div>
                      <div className="mt-3 text-xs uppercase tracking-[0.16em] text-slate-500">
                        Latency {component.latency_ms == null ? '—' : `${component.latency_ms} ms`}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>

            <Card>
              <h2 className="text-lg font-semibold text-white">Version</h2>
              <div className="mt-4 space-y-3 text-sm">
                <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-slate-300">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Version</div>
                  <div className="mt-1 font-semibold text-white">{healthQuery.data.version.version}</div>
                </div>
                <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-slate-300">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Git Commit</div>
                  <div className="mt-1 font-mono text-xs text-white">{healthQuery.data.version.git_commit}</div>
                </div>
                <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-slate-300">
                  <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Build Date</div>
                  <div className="mt-1 text-white">{healthQuery.data.version.build_date}</div>
                </div>
              </div>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}
