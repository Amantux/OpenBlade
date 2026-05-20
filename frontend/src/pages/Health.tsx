import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  acknowledgeTicket,
  dismissAlert,
  getAlerts,
  getEvents,
  getRasTickets,
  getSystemHealth,
} from '../api/health';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

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

function overallStyle(overall: string): string {
  switch (overall) {
    case 'ONLINE':
      return 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300';
    case 'DEGRADED':
      return 'border-amber-500/30 bg-amber-500/15 text-amber-300';
    default:
      return 'border-red-500/30 bg-red-500/15 text-red-300';
  }
}

function severityIcon(severity: string): string {
  switch (severity.toLowerCase()) {
    case 'critical':
      return '🔴';
    case 'warning':
      return '🟡';
    default:
      return '🟢';
  }
}

export default function Health() {
  const queryClient = useQueryClient();
  const [severityFilter, setSeverityFilter] = useState('all');
  const [eventLimit, setEventLimit] = useState(20);

  const systemQuery = useQuery({ queryKey: ['health-dashboard', 'system'], queryFn: getSystemHealth, refetchInterval: 30_000 });
  const ticketsQuery = useQuery({ queryKey: ['health-dashboard', 'tickets'], queryFn: getRasTickets, refetchInterval: 30_000 });
  const eventsQuery = useQuery({ queryKey: ['health-dashboard', 'events', eventLimit], queryFn: () => getEvents(eventLimit), refetchInterval: 30_000 });
  const alertsQuery = useQuery({ queryKey: ['health-dashboard', 'alerts'], queryFn: getAlerts, refetchInterval: 30_000 });

  const acknowledgeMutation = useMutation({
    mutationFn: acknowledgeTicket,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['health-dashboard', 'tickets'] });
    },
  });
  const dismissMutation = useMutation({
    mutationFn: dismissAlert,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['health-dashboard', 'alerts'] });
    },
  });

  const filteredTickets = useMemo(() => {
    const tickets = ticketsQuery.data ?? [];
    if (severityFilter === 'all') {
      return tickets;
    }
    return tickets.filter((ticket) => ticket.severity.toLowerCase() === severityFilter);
  }, [severityFilter, ticketsQuery.data]);

  if (systemQuery.isLoading || ticketsQuery.isLoading || eventsQuery.isLoading || alertsQuery.isLoading) {
    return <Spinner />;
  }
  if (systemQuery.isError) {
    return <ErrorMessage error={systemQuery.error} onRetry={() => systemQuery.refetch()} />;
  }
  if (ticketsQuery.isError) {
    return <ErrorMessage error={ticketsQuery.error} onRetry={() => ticketsQuery.refetch()} />;
  }
  if (eventsQuery.isError) {
    return <ErrorMessage error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }
  if (alertsQuery.isError) {
    return <ErrorMessage error={alertsQuery.error} onRetry={() => alertsQuery.refetch()} />;
  }

  const health = systemQuery.data ?? {
    overall: 'OFFLINE',
    drivesOnline: 0,
    drivesTotal: 0,
    slotsUsed: 0,
    slotsTotal: 0,
    activeJobs: 0,
    openTickets: 0,
    uptime: 0,
    uptimeFormatted: '0m',
    lastBackupTime: null,
    lastBackupStatus: null,
    backend: 'AML API',
    activeAlerts: 0,
    componentStates: {},
  };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Health & Diagnostics</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">System health dashboard</h1>
            <p className="mt-2 text-sm text-slate-400">Monitor system state, open RAS cases, alerts, and the recent AML event stream.</p>
          </div>
          <span className={`inline-flex rounded-full border px-4 py-2 text-sm font-semibold ${overallStyle(health.overall)}`}>
            Overall System · {health.overall}
          </span>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[
            ['Drives Online', `${health.drivesOnline}/${health.drivesTotal}`],
            ['Slots Used / Total', `${health.slotsUsed}/${health.slotsTotal}`],
            ['Active Jobs', String(health.activeJobs)],
            ['Open Tickets', String(health.openTickets)],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
              <div className="mt-2 text-2xl font-semibold text-white">{value}</div>
            </div>
          ))}
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-sm text-slate-300">
            System uptime: <span className="font-semibold text-white">{health.uptimeFormatted}</span>
          </div>
          <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-sm text-slate-300">
            Last backup time: <span className="font-semibold text-white">{formatDate(health.lastBackupTime ?? '')}</span>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.2fr,0.8fr]">
        <Card>
          <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">RAS Tickets</h2>
              <p className="mt-1 text-sm text-slate-400">Auto-refreshing every 30 seconds.</p>
            </div>
            <select value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
              <option value="all">All severities</option>
              <option value="critical">Critical</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
          </div>
          <div className="overflow-x-auto rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">ID</th>
                  <th className="px-4 py-3 font-medium">Severity</th>
                  <th className="px-4 py-3 font-medium">Component</th>
                  <th className="px-4 py-3 font-medium">Message</th>
                  <th className="px-4 py-3 font-medium">Opened</th>
                  <th className="px-4 py-3 font-medium">State</th>
                  <th className="px-4 py-3 font-medium">Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredTickets.map((ticket, index) => (
                  <tr key={ticket.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 font-mono text-xs text-slate-200">{ticket.id}</td>
                    <td className="px-4 py-3"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${severityStyle(ticket.severity)}`}>{ticket.severity}</span></td>
                    <td className="px-4 py-3 text-slate-300">{ticket.component}</td>
                    <td className="px-4 py-3 text-slate-300">{ticket.message}</td>
                    <td className="px-4 py-3 text-slate-300">{formatDate(ticket.opened)}</td>
                    <td className="px-4 py-3 text-slate-300">{ticket.state}</td>
                    <td className="px-4 py-3">
                      <Button
                        variant="secondary"
                        className="px-3 py-1.5"
                        disabled={ticket.state.toLowerCase() !== 'open' || acknowledgeMutation.isPending}
                        onClick={() => acknowledgeMutation.mutate(ticket.id)}
                      >
                        Ack
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-white">Alerts</h2>
          <div className="mt-4 space-y-3">
            {(alertsQuery.data ?? []).map((alert) => (
              <div key={alert.id} className={`rounded-md border px-4 py-4 ${severityStyle(alert.severity)}`}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold">{alert.component}</div>
                    <div className="mt-1 text-sm">{alert.message}</div>
                    <div className="mt-2 text-xs opacity-75">{formatDate(alert.timestamp)}</div>
                  </div>
                  <Button variant="ghost" className="px-3 py-1.5" onClick={() => dismissMutation.mutate(alert.id)}>
                    Dismiss
                  </Button>
                </div>
              </div>
            ))}
            {(alertsQuery.data ?? []).length === 0 ? <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">No active alerts.</div> : null}
          </div>
        </Card>
      </div>

      <Card>
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-white">Recent Events</h2>
            <p className="mt-1 text-sm text-slate-400">Chronological AML event stream.</p>
          </div>
          <Button variant="secondary" onClick={() => setEventLimit((current) => current + 20)}>
            Load more
          </Button>
        </div>
        <div className="space-y-3">
          {(eventsQuery.data ?? []).map((event) => (
            <div key={event.id} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                  <div className="text-sm font-semibold text-slate-100">{severityIcon(event.severity)} {event.component}</div>
                  <div className="mt-1 text-sm text-slate-300">{event.message}</div>
                </div>
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">{formatDate(event.timestamp)}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {acknowledgeMutation.isError ? <ErrorMessage error={acknowledgeMutation.error} /> : null}
      {dismissMutation.isError ? <ErrorMessage error={dismissMutation.error} /> : null}
    </div>
  );
}
