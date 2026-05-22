import { Fragment, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getSafetyStatus, listTapeOperations, runSafetyCheck, type SafetyStatus, type TapeOperation } from '../api/safety';
import { formatDate, formatDuration, toTitleCase, type BadgeVariant } from '../lib/utils';

function tapeOpVariant(status: string): BadgeVariant {
  switch (status.toLowerCase()) {
    case 'completed':
      return 'green';
    case 'queued':
    case 'running':
      return 'blue';
    case 'failed':
      return 'red';
    case 'cancelled':
    case 'skipped':
    default:
      return 'gray';
  }
}

function safetyVariant(status: SafetyStatus['status']): BadgeVariant {
  switch (status) {
    case 'ok':
      return 'green';
    case 'failed':
      return 'red';
    case 'warning':
    default:
      return 'amber';
  }
}

function opDuration(record: TapeOperation): string {
  const start = new Date(record.started_at ?? record.created_at).getTime();
  const end = new Date(record.completed_at ?? record.created_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) {
    return '—';
  }
  return formatDuration(Math.round((end - start) / 1000));
}

export default function AdminSafetyPage() {
  const queryClient = useQueryClient();
  const [expandedOpId, setExpandedOpId] = useState<string>('');

  const tapeOpsQuery = useQuery({
    queryKey: ['admin', 'safety', 'tape-ops'],
    queryFn: () => listTapeOperations(100),
    refetchInterval: 30_000,
  });
  const safetyStatusQuery = useQuery({
    queryKey: ['admin', 'safety', 'status'],
    queryFn: getSafetyStatus,
    refetchInterval: 30_000,
  });

  const runCheckMutation = useMutation({
    mutationFn: runSafetyCheck,
    onSuccess: async (result) => {
      queryClient.setQueryData(['admin', 'safety', 'status'], result);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'safety', 'tape-ops'] });
    },
  });

  const queryError = tapeOpsQuery.error ?? safetyStatusQuery.error;
  const operations = useMemo(() => tapeOpsQuery.data ?? [], [tapeOpsQuery.data]);

  if ((tapeOpsQuery.isLoading || safetyStatusQuery.isLoading) && !queryError) {
    return <Spinner />;
  }

  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void tapeOpsQuery.refetch();
      void safetyStatusQuery.refetch();
    }} />;
  }

  const safetyStatus = safetyStatusQuery.data;

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Admin</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Safety</h1>
            <p className="mt-2 text-sm text-slate-400">Review recent tape operations, check the latest SAFETY_003 status, and jump to system error code guidance.</p>
          </div>
          <Button variant="secondary" disabled={runCheckMutation.isPending} onClick={() => runCheckMutation.mutate()}>
            {runCheckMutation.isPending ? 'Running…' : 'Run Safety Check'}
          </Button>
        </div>
      </Card>

      {safetyStatus ? (
        <Card className="border-amber-500/20 bg-amber-950/10">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Safety Status</div>
              <div className="mt-2 flex items-center gap-3">
                <h2 className="text-lg font-semibold text-white">Run Safety Check</h2>
                <Badge variant={safetyVariant(safetyStatus.status)}>{safetyStatus.status.toUpperCase()}</Badge>
              </div>
              <p className="mt-2 text-sm text-slate-300">Structured safety verification for orchestrator routing, import guard coverage, and destructive action confirmation.</p>
            </div>
            <Badge variant="blue">{safetyStatus.checks.length} checks</Badge>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            {safetyStatus.checks.map((check) => (
              <div key={check.name} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-sm text-slate-300">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold text-white">{check.name}</div>
                  <Badge variant={safetyVariant(check.status)}>{check.status.toUpperCase()}</Badge>
                </div>
                <div className="mt-2 text-slate-400">{check.message}</div>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1.25fr,0.75fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">Tape Operations Log</h2>
              <p className="mt-1 text-sm text-slate-400">Click a row for full details including errors and backend result payloads.</p>
            </div>
            <Badge variant="blue">{operations.length}</Badge>
          </div>

          {operations.length === 0 ? (
            <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
              No tape operations recorded.
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Op ID</th>
                    <th className="px-4 py-3 font-medium">Operation</th>
                    <th className="px-4 py-3 font-medium">Drive</th>
                    <th className="px-4 py-3 font-medium">Barcode</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Started At</th>
                    <th className="px-4 py-3 font-medium">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {operations.map((operation, index) => {
                    const expanded = expandedOpId === operation.op_id;
                    return (
                      <Fragment key={operation.op_id}>
                        <tr
                          className={`${index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'} cursor-pointer hover:bg-quantum-sidebar/60`}
                          onClick={() => setExpandedOpId((current) => (current === operation.op_id ? '' : operation.op_id))}
                        >
                          <td className="px-4 py-3 font-mono text-xs text-slate-200">{operation.op_id}</td>
                          <td className="px-4 py-3 text-slate-200">{toTitleCase(operation.op_type)}</td>
                          <td className="px-4 py-3 text-slate-300">{operation.drive_id ?? '—'}</td>
                          <td className="px-4 py-3 text-slate-300">{operation.barcode || '—'}</td>
                          <td className="px-4 py-3"><Badge variant={tapeOpVariant(operation.status)}>{toTitleCase(operation.status)}</Badge></td>
                          <td className="px-4 py-3 text-slate-300">{formatDate(operation.started_at ?? operation.created_at)}</td>
                          <td className="px-4 py-3 text-slate-300">{opDuration(operation)}</td>
                        </tr>
                        {expanded ? (
                          <tr className="bg-quantum-sidebar/40 text-slate-200">
                            <td className="px-4 py-4" colSpan={7}>
                              <div className="grid gap-4 lg:grid-cols-2">
                                <div className="space-y-3">
                                  <div>
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Requested By</div>
                                    <div className="mt-1 text-sm text-white">{operation.requested_by}</div>
                                  </div>
                                  <div>
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape Path</div>
                                    <div className="mt-1 break-all font-mono text-xs text-slate-200">{operation.tape_path || '—'}</div>
                                  </div>
                                  <div>
                                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Checksum</div>
                                    <div className="mt-1 break-all font-mono text-xs text-slate-200">{operation.checksum_sha256 || '—'}</div>
                                  </div>
                                </div>
                                <div className="space-y-3">
                                  {operation.error ? (
                                    <div className="rounded-md border border-red-500/30 bg-red-950/30 px-3 py-3 text-sm text-red-100">
                                      <div className="font-semibold">Failure Detail</div>
                                      <div className="mt-1">{operation.error}</div>
                                    </div>
                                  ) : null}
                                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-3 text-xs text-slate-300">
                                    <div className="mb-2 text-xs uppercase tracking-[0.16em] text-slate-500">Result</div>
                                    <pre className="whitespace-pre-wrap font-mono">{JSON.stringify(operation.result, null, 2) || '{}'}</pre>
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Quick Reference</div>
          <h2 className="mt-1 text-lg font-semibold text-white">Operator Safety Notes</h2>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              Review failed tape operations before retrying media actions.
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              Confirm the target barcode and mounted drive match the intended action.
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
              Use the error code reference for corrective actions and escalation guidance.
            </div>
          </div>
          <div className="mt-4">
            <Link to="/system/error-codes" className="inline-flex rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-2 text-sm font-semibold text-slate-100 transition hover:bg-quantum-north">
              Open Error Code Quick Reference
            </Link>
          </div>
        </Card>
      </div>
    </div>
  );
}
