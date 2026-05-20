import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getEvents } from '../api/health';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { downloadCsv, formatDate } from '../lib/utils';

const WINDOWS: Record<string, number> = {
  '1h': 1,
  '6h': 6,
  '24h': 24,
  '7d': 24 * 7,
};

export default function ReportsEvents() {
  const [windowKey, setWindowKey] = useState<'1h' | '6h' | '24h' | '7d'>('24h');
  const [severity, setSeverity] = useState('all');
  const [component, setComponent] = useState('all');
  const [search, setSearch] = useState('');

  const eventsQuery = useQuery({ queryKey: ['reports', 'events'], queryFn: () => getEvents(500), refetchInterval: 30_000 });

  const components = useMemo(() => Array.from(new Set((eventsQuery.data ?? []).map((event) => event.component))).sort(), [eventsQuery.data]);
  const filtered = useMemo(() => {
    const cutoff = Date.now() - WINDOWS[windowKey] * 60 * 60 * 1000;
    return (eventsQuery.data ?? []).filter((event) => {
      const time = new Date(event.timestamp).getTime();
      if (!Number.isNaN(time) && time < cutoff) {
        return false;
      }
      if (severity !== 'all' && event.severity.toLowerCase() !== severity) {
        return false;
      }
      if (component !== 'all' && event.component !== component) {
        return false;
      }
      if (search && !event.message.toLowerCase().includes(search.toLowerCase())) {
        return false;
      }
      return true;
    });
  }, [component, eventsQuery.data, search, severity, windowKey]);

  if (eventsQuery.isLoading) {
    return <Spinner />;
  }
  if (eventsQuery.isError) {
    return <ErrorMessage error={eventsQuery.error} onRetry={() => eventsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
          <div>
            <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Reports</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">Events log</h1>
            <p className="mt-2 text-sm text-slate-400">Search and export AML controller events.</p>
          </div>
          <Button
            variant="secondary"
            onClick={() =>
              downloadCsv('events-log.csv', ['timestamp', 'severity', 'component', 'message'], filtered.map((event) => [event.timestamp, event.severity, event.component, event.message]))
            }
          >
            Export CSV
          </Button>
        </div>
      </Card>

      <Card>
        <div className="grid gap-3 lg:grid-cols-4">
          <select value={windowKey} onChange={(event) => setWindowKey(event.target.value as '1h' | '6h' | '24h' | '7d')} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="1h">Last 1h</option>
            <option value="6h">Last 6h</option>
            <option value="24h">Last 24h</option>
            <option value="7d">Last 7d</option>
          </select>
          <select value={severity} onChange={(event) => setSeverity(event.target.value)} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="all">All severities</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <select value={component} onChange={(event) => setComponent(event.target.value)} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
            <option value="all">All components</option>
            {components.map((entry) => <option key={entry} value={entry}>{entry}</option>)}
          </select>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search message text" className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500" />
        </div>

        <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full text-sm">
            <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Time</th>
                <th className="px-4 py-3 font-medium">Severity</th>
                <th className="px-4 py-3 font-medium">Component</th>
                <th className="px-4 py-3 font-medium">Message</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((event, index) => (
                <tr key={event.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 text-slate-300">{formatDate(event.timestamp)}</td>
                  <td className="px-4 py-3 text-slate-300">{event.severity}</td>
                  <td className="px-4 py-3 text-slate-300">{event.component}</td>
                  <td className="px-4 py-3 text-slate-300">{event.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
