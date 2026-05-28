import { Fragment, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getRasTickets } from '../api/health';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { downloadCsv, formatDate } from '../lib/utils';

const PAGE_SIZE = 10;

function severityStyle(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical':
      return 'border-red-500/30 bg-red-500/15 text-red-300';
    case 'warning':
      return 'border-amber-500/30 bg-amber-500/15 text-amber-300';
    default:
      return 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300';
  }
}

export default function ReportsRas() {
  const [severity, setSeverity] = useState('all');
  const [component, setComponent] = useState('all');
  const [stateFilter, setStateFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<string>();

  const rasQuery = useQuery({ queryKey: ['reports', 'ras'], queryFn: () => getRasTickets(), refetchInterval: 30_000 });

  const components = useMemo(() => Array.from(new Set((rasQuery.data ?? []).map((ticket) => ticket.component))).sort(), [rasQuery.data]);
  const filtered = useMemo(() => {
    return (rasQuery.data ?? []).filter((ticket) => {
      if (severity !== 'all' && ticket.severity.toLowerCase() !== severity) {
        return false;
      }
      if (component !== 'all' && ticket.component !== component) {
        return false;
      }
      if (stateFilter !== 'all' && ticket.state.toLowerCase() !== stateFilter) {
        return false;
      }
      return true;
    });
  }, [component, rasQuery.data, severity, stateFilter]);

  const totalPages = Math.max(Math.ceil(filtered.length / PAGE_SIZE), 1);
  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  if (rasQuery.isLoading) {
    return <Spinner />;
  }
  if (rasQuery.isError) {
    return <ErrorMessage error={rasQuery.error} onRetry={() => rasQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Reports</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">RAS tickets</h1>
            <p className="mt-2 text-sm text-slate-400">Filter, review, and export AML reliability tickets.</p>
          </div>
          <Button
            variant="secondary"
            onClick={() =>
              downloadCsv('ras-tickets.csv', ['id', 'severity', 'component', 'message', 'opened', 'state'], filtered.map((ticket) => [ticket.id, ticket.severity, ticket.component, ticket.message, ticket.opened, ticket.state]))
            }
          >
            Export CSV
          </Button>
        </div>
      </Card>

      <Card>
        <div className="grid gap-3 md:grid-cols-3">
          <select value={severity} onChange={(event) => { setSeverity(event.target.value); setPage(1); }} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="all">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <select value={component} onChange={(event) => { setComponent(event.target.value); setPage(1); }} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="all">All components</option>
            {components.map((entry) => <option key={entry} value={entry}>{entry}</option>)}
          </select>
          <select value={stateFilter} onChange={(event) => { setStateFilter(event.target.value); setPage(1); }} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="all">All states</option>
            <option value="open">Open</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="resolved">Resolved</option>
            <option value="closed">Closed</option>
          </select>
        </div>

        <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full text-sm">
            <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">ID</th>
                <th className="px-4 py-3 font-medium">Severity</th>
                <th className="px-4 py-3 font-medium">Component</th>
                <th className="px-4 py-3 font-medium">Message</th>
                <th className="px-4 py-3 font-medium">Opened</th>
                <th className="px-4 py-3 font-medium">State</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((ticket, index) => (
                <Fragment key={ticket.id}>
                  <tr className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'} onClick={() => setExpandedId((current) => current === ticket.id ? undefined : ticket.id)}>
                    <td className="px-4 py-3 font-mono text-xs text-slate-200">{ticket.id}</td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${severityStyle(ticket.severity)}`}>{ticket.severity}</span></td>
                    <td className="px-4 py-3 text-slate-300">{ticket.component}</td>
                    <td className="px-4 py-3 text-slate-300">{ticket.message}</td>
                    <td className="px-4 py-3 text-slate-300">{formatDate(ticket.opened)}</td>
                    <td className="px-4 py-3 text-slate-300">{ticket.state}</td>
                  </tr>
                  {expandedId === ticket.id ? (
                    <tr className="bg-quantum-panel">
                      <td colSpan={6} className="px-4 py-4 text-sm text-slate-300">
                        <div className="grid gap-3 md:grid-cols-2">
                          <div>Resolution: <span className="text-white">{ticket.resolution ?? 'Pending'}</span></div>
                          <div>Assignee: <span className="text-white">{ticket.assignee ?? 'Unassigned'}</span></div>
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex items-center justify-between text-sm text-slate-300">
          <span>Page {page} of {totalPages}</span>
          <div className="flex gap-2">
            <Button variant="secondary" disabled={page === 1} onClick={() => setPage((current) => current - 1)}>Previous</Button>
            <Button variant="secondary" disabled={page === totalPages} onClick={() => setPage((current) => current + 1)}>Next</Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
