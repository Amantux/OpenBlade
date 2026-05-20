import { useQuery } from '@tanstack/react-query';
import { getDnsConfig, getNetworkInterfaces, getNetworkRoutes, getNtpConfig } from '../api/system';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'up':
    case 'enabled':
    case 'synced':
      return 'green';
    case 'warning':
    case 'manual':
      return 'amber';
    case 'down':
    case 'failed':
      return 'red';
    default:
      return 'gray';
  }
}

export default function SystemNetwork() {
  const interfacesQuery = useQuery({ queryKey: ['system', 'network', 'interfaces'], queryFn: getNetworkInterfaces, refetchInterval: 30_000 });
  const dnsQuery = useQuery({ queryKey: ['system', 'network', 'dns'], queryFn: getDnsConfig, refetchInterval: 60_000 });
  const ntpQuery = useQuery({ queryKey: ['system', 'network', 'ntp'], queryFn: getNtpConfig, refetchInterval: 60_000 });
  const routesQuery = useQuery({ queryKey: ['system', 'network', 'routes'], queryFn: getNetworkRoutes, refetchInterval: 60_000 });

  if ([interfacesQuery, dnsQuery, ntpQuery, routesQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [interfacesQuery, dnsQuery, ntpQuery, routesQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const interfaces = interfacesQuery.data ?? [];
  const dns = dnsQuery.data!;
  const ntp = ntpQuery.data!;
  const routes = routesQuery.data ?? [];

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
        <h1 className="mt-1 text-2xl font-semibold text-slate-100">Network</h1>
        <p className="mt-2 text-sm text-slate-400">Detailed interface inventory, DNS, NTP, and route visibility backed by AML network routes.</p>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {interfaces.map((item) => (
          <Card key={item.name}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{item.type}</div>
                <h2 className="mt-1 text-lg font-semibold text-slate-100">{item.name}</h2>
              </div>
              <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
            </div>
            <div className="mt-4 space-y-3 text-sm text-slate-300">
              <div className="flex items-center justify-between gap-3"><span>IP</span><span className="text-slate-100">{item.ip}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Mask</span><span className="text-slate-100">{item.mask}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Gateway</span><span className="text-slate-100">{item.gateway}</span></div>
              <div className="flex items-center justify-between gap-3"><span>MAC</span><span className="text-slate-100">{item.mac}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Speed</span><span className="text-slate-100">{item.speed}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Duplex</span><span className="text-slate-100">{item.duplex}</span></div>
            </div>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">DNS Configuration</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="flex items-center justify-between gap-3"><span>Primary</span><span className="text-slate-100">{dns.primary}</span></div>
            <div className="flex items-center justify-between gap-3"><span>Secondary</span><span className="text-slate-100">{dns.secondary || '—'}</span></div>
            <div className="flex items-center justify-between gap-3"><span>Search Domain</span><span className="text-right text-slate-100">{dns.search.join(', ') || '—'}</span></div>
            <div className="flex items-center justify-between gap-3"><span>Domain</span><span className="text-slate-100">{dns.domain || '—'}</span></div>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">NTP Configuration</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="flex items-center justify-between gap-3"><span>Sync Status</span><Badge variant={statusVariant(ntp.status)}>{ntp.status}</Badge></div>
            <div className="flex items-center justify-between gap-3"><span>Enabled</span><span className="text-slate-100">{ntp.enabled ? 'Yes' : 'No'}</span></div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Servers</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {ntp.servers.map((server) => <Badge key={server} variant="blue">{server}</Badge>)}
              </div>
            </div>
            <div className="flex items-center justify-between gap-3"><span>Last Sync</span><span className="text-slate-100">{ntp.lastSync ? formatDate(ntp.lastSync) : 'Never'}</span></div>
          </div>
        </Card>
      </div>

      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Routes</div>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Destination</th>
                <th className="px-3 py-3">Mask</th>
                <th className="px-3 py-3">Gateway</th>
                <th className="px-3 py-3">Interface</th>
                <th className="px-3 py-3">Metric</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {routes.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-slate-400">No static routes are configured.</td>
                </tr>
              ) : (
                routes.map((route) => (
                  <tr key={`${route.destination}-${route.gateway}`} className="text-slate-200">
                    <td className="px-3 py-3">{route.destination}</td>
                    <td className="px-3 py-3">{route.mask}</td>
                    <td className="px-3 py-3">{route.gateway}</td>
                    <td className="px-3 py-3">{route.interface}</td>
                    <td className="px-3 py-3">{route.metric}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
