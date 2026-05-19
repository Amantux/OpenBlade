import { useEffect, useMemo, useState } from 'react';
import { Activity, HardDrive } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import OperationsPanel from '../components/panels/OperationsPanel';
import Badge from '../components/ui/Badge';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useHealth } from '../hooks/useHealth';
import { useInventory } from '../hooks/useInventory';
import { useJobs } from '../hooks/useJobs';
import { buildRasTickets, normalizeDrive, type RasTicket } from '../lib/lmc';
import { formatDate } from '../lib/utils';

function severityVariant(severity: RasTicket['severity']): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (severity) {
    case 1:
      return 'red';
    case 2:
      return 'amber';
    default:
      return 'gray';
  }
}

function tapeAlertState(state: string): string {
  if (['FAULTED', 'FAILED', 'OFFLINE'].includes(state)) {
    return 'Failed';
  }
  if (['BUSY', 'MOUNTING', 'UNMOUNTING'].includes(state)) {
    return 'Degraded';
  }
  return 'Nominal';
}

export default function Health() {
  const healthQuery = useHealth();
  const inventoryQuery = useInventory();
  const jobsQuery = useJobs();
  const [selectedTicketId, setSelectedTicketId] = useState<string>();

  const inventory = inventoryQuery.data ?? { library_id: 'LIBRARY-01', slots: [], drives: [], changer_state: 'UNKNOWN' };
  const rasTickets = buildRasTickets(healthQuery.health, inventory, jobsQuery.data ?? []);
  const drives = inventory.drives.map(normalizeDrive);

  useEffect(() => {
    if (!selectedTicketId && rasTickets.length > 0) {
      setSelectedTicketId(rasTickets[0].id);
    }
  }, [rasTickets, selectedTicketId]);

  const selectedTicket = useMemo(
    () => rasTickets.find((ticket) => ticket.id === selectedTicketId) ?? rasTickets[0],
    [rasTickets, selectedTicketId],
  );

  if (healthQuery.isLoading || inventoryQuery.isLoading || jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (healthQuery.isError) {
    return <ErrorMessage error={healthQuery.error} onRetry={() => healthQuery.refetch()} />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <NorthPanel
        title="System Health"
        subtitle="RAS Ticket stream for the library controller and media handling subsystem."
        columns={[
          { key: 'severity', header: 'Severity', render: (row: RasTicket) => <Badge variant={severityVariant(row.severity)}>{row.severity}</Badge> },
          { key: 'component', header: 'Component', render: (row: RasTicket) => row.component },
          { key: 'message', header: 'Message', render: (row: RasTicket) => row.message },
          { key: 'time', header: 'Time', render: (row: RasTicket) => formatDate(row.time) },
        ]}
        rows={rasTickets}
        getRowId={(row) => row.id}
        selectedId={selectedTicket?.id}
        onSelect={(row) => setSelectedTicketId(row.id)}
      />

      <InformationPanel
        title={selectedTicket ? `RAS Ticket ${selectedTicket.id}` : 'RAS Ticket Details'}
        subtitle="Selected alert detail and system summary."
        items={[
          { label: 'Severity', value: selectedTicket?.severity ?? '—' },
          { label: 'Component', value: selectedTicket?.component ?? '—' },
          { label: 'Message', value: selectedTicket?.message ?? '—' },
          { label: 'Time', value: selectedTicket ? formatDate(selectedTicket.time) : '—' },
          { label: 'Backend', value: healthQuery.health?.backend ?? '—' },
          { label: 'Average Latency', value: `${healthQuery.avgLatency} ms` },
        ]}
      />

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card className="bg-quantum-info">
          <div className="flex items-center gap-2 text-slate-100">
            <Activity className="h-4 w-4 text-quantum-red" />
            <h2 className="text-lg font-semibold">API Latency</h2>
          </div>
          <div className="mt-4 h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={healthQuery.latencies}>
                <XAxis dataKey="index" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <Tooltip contentStyle={{ backgroundColor: '#1a1f2e', border: '1px solid #2d3748' }} />
                <Line type="monotone" dataKey="latency" stroke="#CC0000" strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="bg-quantum-info">
          <div className="flex items-center gap-2 text-slate-100">
            <HardDrive className="h-4 w-4 text-quantum-red" />
            <h2 className="text-lg font-semibold">TapeAlert / Drive Health</h2>
          </div>
          <div className="mt-4 overflow-hidden rounded-md border border-quantum-border">
            <table className="min-w-full text-sm">
              <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                <tr>
                  <th className="px-4 py-3 font-medium">Drive</th>
                  <th className="px-4 py-3 font-medium">Type</th>
                  <th className="px-4 py-3 font-medium">TapeAlert</th>
                  <th className="px-4 py-3 font-medium">Mount</th>
                </tr>
              </thead>
              <tbody>
                {drives.map((drive, index) => (
                  <tr key={drive.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                    <td className="px-4 py-3 text-slate-200">Drive {drive.id}</td>
                    <td className="px-4 py-3 text-slate-300">{drive.type}</td>
                    <td className="px-4 py-3"><Badge variant={severityVariant(tapeAlertState(drive.state) === 'Failed' ? 1 : tapeAlertState(drive.state) === 'Degraded' ? 2 : 3)}>{tapeAlertState(drive.state)}</Badge></td>
                    <td className="px-4 py-3 text-slate-300">{drive.mountState}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      <OperationsPanel
        title="System Operations"
        subtitle="Refresh health telemetry and active inventory status."
        actions={[
          { label: 'Refresh Health', onClick: () => void healthQuery.refetch(), variant: 'primary' },
          { label: 'Refresh Inventory', onClick: () => void inventoryQuery.refetch(), variant: 'secondary' },
        ]}
      />
    </div>
  );
}
