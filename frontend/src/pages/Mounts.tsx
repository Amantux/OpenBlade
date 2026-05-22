import { useQuery } from '@tanstack/react-query';
import { listPolicies, listPools, listShares } from '../api/nas';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function Mounts() {
  const sharesQuery = useQuery({ queryKey: ['nas', 'shares'], queryFn: listShares, refetchInterval: 30_000 });
  const poolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listPools, refetchInterval: 30_000 });
  const policiesQuery = useQuery({ queryKey: ['nas', 'policies'], queryFn: listPolicies, refetchInterval: 30_000 });

  const queryError = sharesQuery.error ?? poolsQuery.error ?? policiesQuery.error;
  if ([sharesQuery, poolsQuery, policiesQuery].some((query) => query.isLoading && !query.data)) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void sharesQuery.refetch();
      void poolsQuery.refetch();
      void policiesQuery.refetch();
    }} />;
  }

  const shares = sharesQuery.data ?? [];
  const pools = poolsQuery.data ?? [];
  const policies = policiesQuery.data ?? [];

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">File Sharing</h1>
            <p className="mt-2 text-sm text-slate-400">Review configured NAS shares, their default storage policies, and the virtual pool destinations exposed to clients.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="blue">{shares.length} shares</Badge>
            <Badge variant="purple">{policies.length} policies</Badge>
            <Badge variant="green">{pools.length} pools</Badge>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Share definitions</div>
          <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3">Share</th>
                  <th className="px-4 py-3">Path</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Policy</th>
                  <th className="px-4 py-3">Mode</th>
                </tr>
              </thead>
              <tbody>
                {shares.map((share, index) => {
                  const policyName = policies.find((policy) => policy.id === share.default_policy_id)?.name ?? 'Inherited';
                  return (
                    <tr key={share.path} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                      <td className="px-4 py-3 text-slate-100">{share.name}</td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-300">{share.path}</td>
                      <td className="px-4 py-3 text-slate-300">{share.share_type}</td>
                      <td className="px-4 py-3 text-slate-300">{policyName}</td>
                      <td className="px-4 py-3 text-slate-300">{share.writable ? 'Read / Write' : 'Read Only'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Virtual pool mounts</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            {pools.map((pool) => (
              <div key={pool.pool_id} className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold text-slate-100">{pool.name}</div>
                  <Badge variant={pool.virtual_mount_enabled ? 'green' : 'gray'}>{pool.virtual_mount_enabled ? 'Mounted' : 'Disabled'}</Badge>
                </div>
                <div className="mt-2 space-y-1 text-xs text-slate-400">
                  <div>Mount path: {pool.mount_path}</div>
                  <div>Restore target: {pool.restore_target}</div>
                  <div>Access: {pool.access_mode} · {pool.hydration_behavior}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}
