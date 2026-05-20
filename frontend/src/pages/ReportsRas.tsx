import { useQuery } from '@tanstack/react-query';
import { getRasTickets } from '../api/reports';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function ReportsRas() {
  const rasQuery = useQuery({ queryKey: ['reports', 'ras'], queryFn: getRasTickets, refetchInterval: 30_000 });

  if (rasQuery.isLoading) {
    return <Spinner />;
  }

  if (rasQuery.isError) {
    return <ErrorMessage error={rasQuery.error} onRetry={() => rasQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="Reports"
      title="RAS Tickets"
      description="Live RAS ticket inventory is available now with full case workflows following in the next build."
    >
      <Card className="bg-quantum-info">
        <div className="space-y-3">
          {(rasQuery.data ?? []).map((ticket) => (
            <div key={ticket.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
              {ticket.id} · {ticket.summary} · {ticket.status}
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
