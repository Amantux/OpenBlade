import { useQuery } from '@tanstack/react-query';
import { getActivity } from '../api/reports';
import StubPage from '../components/pages/StubPage';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getJobTypeLabel } from '../lib/lmc';

export default function ReportsActivity() {
  const activityQuery = useQuery({ queryKey: ['reports', 'activity'], queryFn: getActivity, refetchInterval: 30_000 });

  if (activityQuery.isLoading) {
    return <Spinner />;
  }

  if (activityQuery.isError) {
    return <ErrorMessage error={activityQuery.error} onRetry={() => activityQuery.refetch()} />;
  }

  return (
    <StubPage
      eyebrow="Reports"
      title="Activity"
      description="Completed and failed AML job history is live, with richer analytics and export options queued next."
    >
      <Card className="bg-quantum-info">
        <div className="space-y-3">
          {(activityQuery.data ?? []).map((job) => (
            <div key={job.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
              {job.id} · {getJobTypeLabel(job)} · {job.status}
            </div>
          ))}
        </div>
      </Card>
    </StubPage>
  );
}
