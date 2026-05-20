import { useQuery } from '@tanstack/react-query';
import { getSystemDetail, getSystemOverview, getSystemStatus, getSystemUptime, getSystemVersion } from '../api/system';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import { formatDate, formatDuration } from '../lib/utils';
import Spinner from '../components/ui/Spinner';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'good':
      return 'green';
    case 'warning':
      return 'amber';
    case 'failed':
      return 'red';
    default:
      return 'gray';
  }
}

export default function System() {
  const overviewQuery = useQuery({ queryKey: ['system', 'overview'], queryFn: getSystemOverview, refetchInterval: 30_000 });
  const detailQuery = useQuery({ queryKey: ['system', 'detail'], queryFn: getSystemDetail, refetchInterval: 60_000 });
  const statusQuery = useQuery({ queryKey: ['system', 'status'], queryFn: getSystemStatus, refetchInterval: 15_000 });
  const versionQuery = useQuery({ queryKey: ['system', 'version'], queryFn: getSystemVersion, refetchInterval: 60_000 });
  const uptimeQuery = useQuery({ queryKey: ['system', 'uptime'], queryFn: getSystemUptime, refetchInterval: 15_000 });

  if ([overviewQuery, detailQuery, statusQuery, versionQuery, uptimeQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [overviewQuery, detailQuery, statusQuery, versionQuery, uptimeQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const overview = overviewQuery.data;
  const detail = detailQuery.data;
  const status = statusQuery.data;
  const version = versionQuery.data;
  const uptime = uptimeQuery.data;

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">System Info</h1>
            <p className="mt-2 text-sm text-slate-400">Live AML system cards backed by /aml/system, /aml/system/info, and related routes.</p>
          </div>
          <Badge variant={statusVariant(status?.overall ?? 'unknown')}>{status?.overall ?? 'unknown'}</Badge>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          {[
            ['Hostname', overview?.hostname ?? '—'],
            ['Version', `${version?.software ?? '—'} / ${version?.firmware ?? '—'}`],
            ['Uptime', uptime ? `${uptime.formatted} (${formatDuration(overview?.uptime)})` : '—'],
            ['Installed', detail?.installedDate ? formatDate(detail.installedDate) : '—'],
            ['Status', status?.overall ?? '—'],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
              <div className="mt-2 text-sm font-medium text-slate-100">{value}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="bg-quantum-info">
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Hardware</div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {[
              ['Model', overview?.model ?? '—'],
              ['Serial', overview?.serialNumber ?? '—'],
              ['CPU', detail?.cpuModel ?? '—'],
              ['CPU Count', detail?.cpuCount ?? '—'],
              ['Memory', `${detail?.totalMem ?? '—'} GB`],
              ['Disk', `${detail?.totalDisk ?? '—'} GB`],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
                <div className="mt-2 text-sm text-slate-100">{value}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="bg-quantum-info">
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Subsystem health</div>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {[
              ['CPU', status?.cpu ?? '—'],
              ['Memory', status?.memory ?? '—'],
              ['Disk', status?.disk ?? '—'],
              ['Network', status?.network ?? '—'],
              ['Services', status?.services ?? '—'],
              ['Boot Time', uptime?.bootTime ? formatDate(uptime.bootTime) : '—'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
                <div className="mt-2 text-sm text-slate-100">{value}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
