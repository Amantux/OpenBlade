import { useQuery } from '@tanstack/react-query';
import { getEvents } from '../api/reports';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';

export default function ReportsEvents() {
  const eventsQuery = useQuery({ queryKey: ['reports', 'events'], queryFn: getEvents, refetchInterval: 30_000 });

  if (eventsQuery.isLoading) {
    return <Spinner />;
  }

  if (eventsQuery.isError) {
    return <ErrorMessage error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="Reports"
      title="Events Log"
      description="Recent AML events are visible today; filtering, export, and acknowledgement flows are next."
    >
      <Card className="bg-quantum-info">
        <div className="space-y-3">
          {(eventsQuery.data ?? []).map((event) => (
            <div key={event.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
              {event.timestamp} · {event.severity} · {event.message}
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
