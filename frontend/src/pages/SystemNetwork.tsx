import { useQuery } from '@tanstack/react-query';
import { getNetworkConfig } from '../api/system';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function SystemNetwork() {
  const networkQuery = useQuery({ queryKey: ['system', 'network'], queryFn: getNetworkConfig, refetchInterval: 60_000 });

  if (networkQuery.isLoading) {
    return <Spinner />;
  }

  if (networkQuery.isError) {
    return <ErrorMessage error={networkQuery.error} onRetry={() => networkQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="System"
      title="Network"
      description="Current interface inventory is live; editing and change workflows are queued for the next build."
    >
      <Card className="bg-quantum-info">
        <div className="grid gap-3 lg:grid-cols-2">
          {networkQuery.data?.interfaces.map((item) => (
            <div key={item.name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-sm font-semibold text-slate-100">{item.name}</div>
              <div className="mt-2 text-sm text-slate-400">{item.ip} / {item.mask} · {item.status} · {item.speed}</div>
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
