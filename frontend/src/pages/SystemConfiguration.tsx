import { useQuery } from '@tanstack/react-query';
import { getSystemConfig } from '../api/system';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function SystemConfiguration() {
  const configQuery = useQuery({ queryKey: ['system', 'config'], queryFn: getSystemConfig, refetchInterval: 60_000 });

  if (configQuery.isLoading) {
    return <Spinner />;
  }

  if (configQuery.isError) {
    return <ErrorMessage error={configQuery.error} onRetry={() => configQuery.refetch()} />;
  }

  const config = configQuery.data;

  return (
    <StubPage
      eyebrow="System"
      title="Configuration"
      description="Live configuration is visible now; write flows and config drift tools are queued for the next build."
    >
      <Card className="bg-quantum-info">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
          {[
            ['Hostname', config?.hostname ?? '—'],
            ['Timezone', config?.timezone ?? '—'],
            ['Locale', config?.locale ?? '—'],
            ['Date Format', config?.dateFormat ?? '—'],
            ['Temperature', config?.temperatureUnit ?? '—'],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
              <div className="mt-2 text-sm text-slate-100">{value}</div>
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
