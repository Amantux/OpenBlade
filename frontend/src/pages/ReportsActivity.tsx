import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getAuditLog, getEvents } from '../api/health';
import { listJobHistory } from '../api/operations';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

interface ActivityRow {
  id: string;
  timestamp: string;
  actor: string;
  category: string;
  component: string;
  message: string;
  state: string;
}

export default function ReportsActivity() {
  const jobsQuery = useQuery({ queryKey: ['reports', 'activity', 'jobs'], queryFn: listJobHistory, refetchInterval: 30_000 });
  const eventsQuery = useQuery({ queryKey: ['reports', 'activity', 'events'], queryFn: () => getEvents(200), refetchInterval: 30_000 });
  const auditQuery = useQuery({ queryKey: ['reports', 'activity', 'audit'], queryFn: () => getAuditLog(200), refetchInterval: 30_000 });

  const activity = useMemo<ActivityRow[]>(() => {
    const jobRows = (jobsQuery.data ?? []).map((job) => ({
      id: `job-${job.id}`,
      timestamp: job.completedAt ?? job.startedAt,
      actor: 'system',
      category: 'Job',
      component: job.type,
      message: `${job.type} job ${job.result ?? job.state.toLowerCase()}`,
      state: job.state,
    }));
    const eventRows = (eventsQuery.data ?? []).map((event) => ({
      id: `event-${event.id}`,
      timestamp: event.timestamp,
      actor: 'system',
      category: 'Event',
      component: event.component,
      message: event.message,
      state: event.severity,
    }));
    const auditRows = (auditQuery.data ?? []).map((entry, index) => ({
      id: `audit-${index}-${entry.timestamp}`,
      timestamp: entry.timestamp,
      actor: entry.user,
      category: 'Audit',
      component: entry.resource,
      message: `${entry.action} ${entry.resource}`,
      state: entry.result,
    }));

    return [...jobRows, ...eventRows, ...auditRows].sort(
      (left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime(),
    );
  }, [auditQuery.data, eventsQuery.data, jobsQuery.data]);

  if (jobsQuery.isLoading || eventsQuery.isLoading || auditQuery.isLoading) {
    return <Spinner />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }
  if (eventsQuery.isError) {
    return <ErrorMessage error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }
  if (auditQuery.isError) {
    return <ErrorMessage error={auditQuery.error} onRetry={() => auditQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Reports</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Activity log</h1>
        <p className="mt-2 text-sm text-slate-400">Combined job history, recent events, and audit records in one chronological feed.</p>
      </Card>

      <Card>
        <div className="overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full text-sm">
            <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Time</th>
                <th className="px-4 py-3 font-medium">Actor</th>
                <th className="px-4 py-3 font-medium">Category</th>
                <th className="px-4 py-3 font-medium">Component</th>
                <th className="px-4 py-3 font-medium">Message</th>
                <th className="px-4 py-3 font-medium">State</th>
              </tr>
            </thead>
            <tbody>
              {activity.map((item, index) => (
                <tr key={item.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 text-slate-300">{formatDate(item.timestamp)}</td>
                  <td className="px-4 py-3 text-slate-300">{item.actor}</td>
                  <td className="px-4 py-3 text-slate-300">{item.category}</td>
                  <td className="px-4 py-3 text-slate-300">{item.component}</td>
                  <td className="px-4 py-3 text-slate-300">{item.message}</td>
                  <td className="px-4 py-3 text-slate-300">{item.state}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
