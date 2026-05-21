import { useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { getArchiveJobs } from '../api/archive';
import {
  getCatalogStatus,
  getErrorCodes,
  getLibraryStatus,
  getSystemHealthDashboard,
} from '../api/catalogAdmin';
import { listActiveJobs } from '../api/operations';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'ok':
    case 'ready':
    case 'true':
      return 'green';
    case 'degraded':
    case 'warning':
      return 'amber';
    case 'unhealthy':
    case 'false':
    case 'error':
      return 'red';
    default:
      return 'blue';
  }
}

export default function System() {
  const dashboardQuery = useQuery({ queryKey: ['system', 'dashboard'], queryFn: getSystemHealthDashboard, refetchInterval: 15_000 });
  const libraryQuery = useQuery({ queryKey: ['system', 'library-status'], queryFn: getLibraryStatus, refetchInterval: 15_000 });
  const catalogQuery = useQuery({ queryKey: ['system', 'catalog-status'], queryFn: getCatalogStatus, refetchInterval: 15_000 });
  const errorCodesQuery = useQuery({ queryKey: ['system', 'error-codes'], queryFn: getErrorCodes, refetchInterval: 60_000 });
  const jobsQuery = useQuery({ queryKey: ['operations', 'jobs', 'active'], queryFn: listActiveJobs, refetchInterval: 5_000 });
  const archiveJobsQuery = useQuery({ queryKey: ['archive', 'jobs'], queryFn: getArchiveJobs, refetchInterval: 10_000 });

  const queryError = dashboardQuery.error ?? libraryQuery.error ?? catalogQuery.error ?? errorCodesQuery.error ?? jobsQuery.error ?? archiveJobsQuery.error;
  if ([dashboardQuery, libraryQuery, catalogQuery, errorCodesQuery, jobsQuery, archiveJobsQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void dashboardQuery.refetch();
      void libraryQuery.refetch();
      void catalogQuery.refetch();
      void errorCodesQuery.refetch();
      void jobsQuery.refetch();
      void archiveJobsQuery.refetch();
    }} />;
  }

  const dashboard = dashboardQuery.data!;
  const library = libraryQuery.data!;
  const catalog = catalogQuery.data!;
  const activeJobs = jobsQuery.data ?? [];
  const archiveJobs = archiveJobsQuery.data ?? [];
  const components = dashboard.health.components ?? [];
  const errorCodeSummary = useMemo(() => {
    const codes = errorCodesQuery.data ?? [];
    return {
      total: codes.length,
      errors: codes.filter((entry) => entry.severity === 'error').length,
      warnings: codes.filter((entry) => entry.severity === 'warning').length,
      infos: codes.filter((entry) => entry.severity === 'info').length,
    };
  }, [errorCodesQuery.data]);

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">System information</h1>
            <p className="mt-2 text-sm text-slate-400">
              Consolidated health, readiness, version, library connectivity, queue depth, database reachability,
              and error code coverage from the Phase 2 root status APIs.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => void dashboardQuery.refetch()}>Refresh</Button>
            <Link to="/system/firmware"><Button variant="secondary">Firmware</Button></Link>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
        <Card><div className="text-xs uppercase tracking-[0.18em] text-slate-500">OpenBlade Version</div><div className="mt-2 text-lg font-semibold text-slate-100">{dashboard.version.version}</div><div className="mt-2 text-sm text-slate-400">Commit {dashboard.version.git_commit}</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.18em] text-slate-500">Health</div><div className="mt-2"><Badge variant={statusVariant(dashboard.health.status)}>{dashboard.health.status}</Badge></div><div className="mt-2 text-sm text-slate-400">Checked {formatDate(dashboard.health.checked_at)}</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.18em] text-slate-500">Readiness</div><div className="mt-2"><Badge variant={statusVariant(String(dashboard.readiness.ready))}>{dashboard.readiness.ready ? 'ready' : 'not ready'}</Badge></div><div className="mt-2 text-sm text-slate-400">{dashboard.readiness.reason || 'All required services are available.'}</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.18em] text-slate-500">Library</div><div className="mt-2"><Badge variant={statusVariant(String(library.library_connected))}>{library.library_connected ? 'connected' : 'disconnected'}</Badge></div><div className="mt-2 text-sm text-slate-400">{library.cartridges_loaded} cartridge(s) loaded</div></Card>
        <Card><div className="text-xs uppercase tracking-[0.18em] text-slate-500">Queue / DB</div><div className="mt-2 text-lg font-semibold text-slate-100">{activeJobs.length} / {catalog.db_reachable ? 'online' : 'offline'}</div><div className="mt-2 text-sm text-slate-400">Active jobs / catalog DB</div></Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Backend probes</div>
              <div className="mt-1 text-lg font-semibold text-slate-100">/healthz and /readyz</div>
            </div>
            <Badge variant="blue">{components.length} components</Badge>
          </div>
          <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3">Component</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Message</th>
                  <th className="px-4 py-3">Latency</th>
                </tr>
              </thead>
              <tbody>
                {components.map((component, index) => (
                  <tr key={component.name} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 text-slate-100">{component.name}</td>
                    <td className="px-4 py-3"><Badge variant={statusVariant(component.status)}>{component.status}</Badge></td>
                    <td className="px-4 py-3 text-slate-300">{component.message}</td>
                    <td className="px-4 py-3 text-slate-300">{component.latency_ms === null ? '—' : `${component.latency_ms.toFixed(2)} ms`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Catalog and queue summary</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 text-sm text-slate-300">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Datasets</div><div className="mt-1 text-slate-100">{catalog.total_datasets}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">File Records</div><div className="mt-1 text-slate-100">{catalog.total_file_records}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Path Mappings</div><div className="mt-1 text-slate-100">{catalog.total_path_mappings}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Archive Jobs</div><div className="mt-1 text-slate-100">{archiveJobs.length}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Last Rebuild</div><div className="mt-1 text-slate-100">{catalog.last_rebuild_status ?? 'None'}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Checked</div><div className="mt-1 text-slate-100">{formatDate(catalog.checked_at)}</div></div>
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr_0.95fr]">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Library connection</div>
          <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3">Drive</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3">Loaded Barcode</th>
                  <th className="px-4 py-3">Last Operation</th>
                </tr>
              </thead>
              <tbody>
                {library.drives.length === 0 ? (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-slate-400">No library drive status returned.</td></tr>
                ) : library.drives.map((drive, index) => (
                  <tr key={`${drive.drive_id ?? drive.drive_index ?? index}`} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 text-slate-100">{drive.drive_id ?? drive.drive_index ?? index + 1}</td>
                    <td className="px-4 py-3"><Badge variant={statusVariant(String(drive.drive_state ?? drive.status ?? 'unknown'))}>{String(drive.drive_state ?? drive.status ?? 'unknown')}</Badge></td>
                    <td className="px-4 py-3 text-slate-300">{drive.loaded_barcode ?? drive.barcode ?? 'Empty'}</td>
                    <td className="px-4 py-3 text-slate-300">{drive.last_operation ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Error code coverage</div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 text-sm text-slate-300">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Total Codes</div><div className="mt-1 text-slate-100">{errorCodeSummary.total}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Errors</div><div className="mt-1 text-slate-100">{errorCodeSummary.errors}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Warnings</div><div className="mt-1 text-slate-100">{errorCodeSummary.warnings}</div></div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3"><div className="text-xs uppercase tracking-[0.16em] text-slate-500">Info</div><div className="mt-1 text-slate-100">{errorCodeSummary.infos}</div></div>
          </div>
          <div className="mt-4 space-y-3">
            {(errorCodesQuery.data ?? []).slice(0, 5).map((entry) => (
              <div key={entry.code} className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-300">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-mono text-slate-100">{entry.code}</span>
                  <Badge variant={statusVariant(entry.severity)}>{entry.severity}</Badge>
                </div>
                <div className="mt-2 text-slate-100">{entry.title}</div>
                <div className="mt-1 text-xs text-slate-400">{entry.action}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
