import { useQuery } from '@tanstack/react-query';
import { getSystemFirmware } from '../api/system';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function SystemFirmware() {
  const firmwareQuery = useQuery({ queryKey: ['system', 'firmware'], queryFn: getSystemFirmware, refetchInterval: 60_000 });

  if (firmwareQuery.isLoading) {
    return <Spinner />;
  }

  if (firmwareQuery.isError) {
    return <ErrorMessage error={firmwareQuery.error} onRetry={() => firmwareQuery.refetch()} />;
  }

  const firmware = firmwareQuery.data;

  return (
    <StubPage
      eyebrow="System"
      title="Firmware"
      description="Current staged firmware information is live while activation and rollout UX are reserved for the next build."
    >
      <Card className="bg-quantum-info">
        <div className="space-y-3">
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
            Current version: <span className="font-semibold text-slate-100">{firmware?.currentVersion ?? '—'}</span>
          </div>
          {(firmware?.uploadedPackages ?? []).map((item) => (
            <div key={item.name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
              {item.name} · {item.version} · {item.active ? 'active' : 'available'}
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
