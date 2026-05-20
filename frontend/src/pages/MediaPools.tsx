import { useQuery } from '@tanstack/react-query';
import { getMediaPools } from '../api/cartridges';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function MediaPools() {
  const poolsQuery = useQuery({ queryKey: ['media', 'pools'], queryFn: getMediaPools, refetchInterval: 60_000 });

  if (poolsQuery.isLoading) {
    return <Spinner />;
  }

  if (poolsQuery.isError) {
    return <ErrorMessage error={poolsQuery.error} onRetry={() => poolsQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="Media"
      title="Media Pools"
      description="NAS-style pool management starts here with live AML pool inventory and richer lifecycle controls next."
    >
      <Card className="bg-quantum-info">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(poolsQuery.data ?? []).map((pool) => (
            <div key={pool.name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-sm font-semibold text-slate-100">{pool.name}</div>
              <div className="mt-2 text-sm text-slate-400">{pool.type} · {pool.mediaCount} media · {pool.policy}</div>
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
