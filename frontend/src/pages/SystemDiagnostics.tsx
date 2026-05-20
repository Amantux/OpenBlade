import { useQuery } from '@tanstack/react-query';
import { getSystemDiagnostics } from '../api/system';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function SystemDiagnostics() {
  const diagnosticsQuery = useQuery({ queryKey: ['system', 'diagnostics'], queryFn: getSystemDiagnostics, refetchInterval: 30_000 });

  if (diagnosticsQuery.isLoading) {
    return <Spinner />;
  }

  if (diagnosticsQuery.isError) {
    return <ErrorMessage error={diagnosticsQuery.error} onRetry={() => diagnosticsQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="System"
      title="Diagnostics"
      description="Latest diagnostic results are shown here while test orchestration controls are planned for the next build."
    >
      <Card className="bg-quantum-info">
        <div className="space-y-3">
          {(diagnosticsQuery.data?.tests ?? []).map((test) => (
            <div key={test.name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-sm font-semibold text-slate-100">{test.name}</div>
              <div className="mt-2 text-sm text-slate-400">{test.result}{test.details ? ` · ${test.details}` : ''}</div>
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
