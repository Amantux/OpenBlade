import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ConfirmDialog from '../components/nas/ConfirmDialog';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import {
  executeCatalogRebuild,
  getCatalogRebuildRun,
  listCatalogRebuildRuns,
  listLoadedRebuildTapes,
  planCatalogRebuild,
  type CatalogRebuildPlan,
  type CatalogRebuildRun,
} from '../api/catalogAdmin';
import { formatDate, toTitleCase } from '../lib/utils';

function statusVariant(status: string): 'blue' | 'green' | 'amber' | 'red' | 'gray' {
  switch (status.toLowerCase()) {
    case 'running':
    case 'in_progress':
      return 'blue';
    case 'completed':
      return 'green';
    case 'failed':
    case 'cancelled':
      return 'red';
    case 'planned':
      return 'amber';
    default:
      return 'gray';
  }
}

function hasActiveRun(runs: CatalogRebuildRun[] | undefined): boolean {
  return (runs ?? []).some((run) => ['running', 'in_progress'].includes(run.status.toLowerCase()));
}

function canExecutePlan(plan?: CatalogRebuildPlan | null): boolean {
  if (!plan) {
    return false;
  }

  return plan.safe_to_enqueue && plan.barcodes_invalid.length === 0 && plan.barcodes_missing_shard.length === 0;
}

export default function CatalogRebuildPage() {
  const queryClient = useQueryClient();
  const [selectedBarcodes, setSelectedBarcodes] = useState<string[]>([]);
  const [useAllLoadedTapes, setUseAllLoadedTapes] = useState(true);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [latestPlan, setLatestPlan] = useState<CatalogRebuildPlan | null>(null);

  const loadedTapesQuery = useQuery({
    queryKey: ['catalog-rebuild', 'loaded-tapes'],
    queryFn: listLoadedRebuildTapes,
  });
  const historyQuery = useQuery({
    queryKey: ['catalog-rebuild', 'runs'],
    queryFn: listCatalogRebuildRuns,
    refetchInterval: (query) => (hasActiveRun(query.state.data as CatalogRebuildRun[] | undefined) ? 10_000 : false),
  });
  const activeRunQuery = useQuery({
    queryKey: ['catalog-rebuild', 'run', activeRunId],
    queryFn: () => getCatalogRebuildRun(activeRunId!),
    enabled: Boolean(activeRunId),
    refetchInterval: (query) => {
      const run = query.state.data as CatalogRebuildRun | undefined;
      return run && ['running', 'in_progress'].includes(run.status.toLowerCase()) ? 10_000 : false;
    },
  });

  const planMutation = useMutation({
    mutationFn: (barcodes: string[]) => planCatalogRebuild(barcodes),
    onSuccess: (result) => {
      setLatestPlan(result);
      setActiveRunId(result.run_id);
      void queryClient.invalidateQueries({ queryKey: ['catalog-rebuild', 'runs'] });
    },
  });
  const executeMutation = useMutation({
    mutationFn: (runId: string) => executeCatalogRebuild(runId),
    onSuccess: async (result) => {
      setShowConfirm(false);
      setActiveRunId(result.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['catalog-rebuild', 'runs'] }),
        queryClient.invalidateQueries({ queryKey: ['catalog-rebuild', 'run', result.id] }),
      ]);
    },
  });

  const loadedTapes = loadedTapesQuery.data ?? [];
  const history = historyQuery.data ?? [];
  const activeRun = activeRunQuery.data ?? history.find((run) => run.id === activeRunId) ?? executeMutation.data;
  const selectedCount = useAllLoadedTapes ? loadedTapes.length : selectedBarcodes.length;
  const planReady = canExecutePlan(latestPlan);

  const detailRun = useMemo(
    () => activeRun ?? history.find((run) => run.id === activeRunId) ?? null,
    [activeRun, activeRunId, history],
  );

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Catalog</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Catalog rebuild</h1>
            <p className="mt-2 text-sm text-slate-400">Preview catalog recovery from tape manifests, then execute a verified rebuild.</p>
          </div>
          <Badge variant={planReady ? 'green' : latestPlan ? 'amber' : 'gray'}>
            {planReady ? 'Ready to execute' : latestPlan ? 'Review required' : 'Awaiting dry-run'}
          </Badge>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">Dry-run plan</h2>
              <p className="mt-1 text-sm text-slate-400">Choose loaded tapes to scan, then generate a non-destructive recovery plan.</p>
            </div>
          </div>

          <div className="mt-5 grid gap-4">
            <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Options</div>
              <label className="mt-3 flex items-center gap-3 text-sm text-slate-200">
                <input
                  type="checkbox"
                  checked={useAllLoadedTapes}
                  onChange={(event) => setUseAllLoadedTapes(event.target.checked)}
                  className="h-4 w-4 rounded border-quantum-border bg-quantum-sidebar"
                />
                All loaded tapes
              </label>
              <label className="mt-2 flex items-center gap-3 text-sm text-slate-500">
                <input type="checkbox" checked disabled className="h-4 w-4 rounded border-quantum-border bg-quantum-sidebar" />
                Dry run only
              </label>
            </div>

            {!useAllLoadedTapes ? (
              <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Loaded tapes</div>
                    <div className="mt-1 text-sm text-slate-400">Select one or more barcodes to include.</div>
                  </div>
                  <Badge variant="gray">{selectedBarcodes.length} selected</Badge>
                </div>
                {loadedTapesQuery.isLoading ? <div className="mt-4"><Spinner /></div> : null}
                {loadedTapesQuery.isError ? <div className="mt-4"><ErrorMessage error={loadedTapesQuery.error} onRetry={() => loadedTapesQuery.refetch()} /></div> : null}
                {!loadedTapesQuery.isLoading && !loadedTapesQuery.isError ? (
                  loadedTapes.length === 0 ? (
                    <div className="mt-4 rounded-md border border-dashed border-quantum-border px-4 py-6 text-sm text-slate-400">
                      No loaded tapes are currently available.
                    </div>
                  ) : (
                    <div className="mt-4 grid gap-2 md:grid-cols-2">
                      {loadedTapes.map((barcode) => (
                        <label key={barcode} className="flex items-center gap-3 rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-200">
                          <input
                            type="checkbox"
                            checked={selectedBarcodes.includes(barcode)}
                            onChange={(event) =>
                              setSelectedBarcodes((current) =>
                                event.target.checked ? [...current, barcode] : current.filter((value) => value !== barcode),
                              )
                            }
                            className="h-4 w-4 rounded border-quantum-border bg-quantum-panel"
                          />
                          {barcode}
                        </label>
                      ))}
                    </div>
                  )
                ) : null}
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm text-slate-400">
                Target tapes: <span className="font-semibold text-slate-200">{selectedCount || 'None selected'}</span>
              </div>
              <Button
                disabled={planMutation.isPending || (!useAllLoadedTapes && selectedBarcodes.length === 0)}
                onClick={() => planMutation.mutate(useAllLoadedTapes ? [] : selectedBarcodes)}
              >
                {planMutation.isPending ? 'Planning…' : 'Run Dry-Run Plan'}
              </Button>
            </div>
          </div>

          {planMutation.isError ? <div className="mt-4"><ErrorMessage error={planMutation.error} /></div> : null}

          {latestPlan ? (
            <div className="mt-6 space-y-4">
              <div className="grid gap-3 md:grid-cols-3">
                <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-emerald-300/80">Datasets recoverable</div>
                  <div className="mt-2 text-2xl font-semibold text-emerald-200">{latestPlan.estimated_datasets}</div>
                </div>
                <div className="rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-emerald-300/80">Files recoverable</div>
                  <div className="mt-2 text-2xl font-semibold text-emerald-200">{latestPlan.estimated_files}</div>
                </div>
                <div className="rounded-md border border-amber-500/30 bg-amber-500/10 px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-amber-300/80">Warnings</div>
                  <div className="mt-2 text-2xl font-semibold text-amber-200">{latestPlan.warnings.length}</div>
                </div>
              </div>

              <div className="grid gap-3 lg:grid-cols-3">
                <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold text-amber-200">Missing manifest</h3>
                    <Badge variant="amber">{latestPlan.barcodes_missing_manifest.length}</Badge>
                  </div>
                  <div className="mt-3 text-sm text-amber-100/90">
                    {latestPlan.barcodes_missing_manifest.length > 0 ? latestPlan.barcodes_missing_manifest.join(', ') : 'None'}
                  </div>
                </div>
                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold text-red-200">Invalid tapes</h3>
                    <Badge variant="red">{latestPlan.barcodes_invalid.length}</Badge>
                  </div>
                  <div className="mt-3 text-sm text-red-100/90">
                    {latestPlan.barcodes_invalid.length > 0 ? latestPlan.barcodes_invalid.join(', ') : 'None'}
                  </div>
                </div>
                <div className="rounded-md border border-red-500/30 bg-red-500/10 p-4">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className="font-semibold text-red-200">Missing shard</h3>
                    <Badge variant="red">{latestPlan.barcodes_missing_shard.length}</Badge>
                  </div>
                  <div className="mt-3 text-sm text-red-100/90">
                    {latestPlan.barcodes_missing_shard.length > 0 ? latestPlan.barcodes_missing_shard.join(', ') : 'None'}
                  </div>
                </div>
              </div>

              <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="font-semibold text-white">Warnings</h3>
                  <Badge variant={latestPlan.safe_to_enqueue ? 'green' : 'red'}>
                    {latestPlan.safe_to_enqueue ? 'Safe to enqueue' : 'Unsafe to enqueue'}
                  </Badge>
                </div>
                {latestPlan.warnings.length === 0 ? (
                  <div className="mt-3 text-sm text-slate-400">No warnings returned.</div>
                ) : (
                  <ul className="mt-3 space-y-2 text-sm text-slate-300">
                    {latestPlan.warnings.map((warning) => (
                      <li key={warning} className="rounded-md border border-amber-500/20 bg-amber-500/10 px-3 py-2">
                        {warning}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          ) : null}
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-white">Execute rebuild</h2>
          <p className="mt-1 text-sm text-slate-400">Run the selected catalog rebuild once the dry-run completes without blocking errors.</p>

          <div className="mt-5 rounded-md border border-quantum-border bg-quantum-panel p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Current plan</div>
                <div className="mt-1 text-sm text-slate-200">{latestPlan ? latestPlan.run_id : 'Run a dry-run plan first'}</div>
              </div>
              <Button variant="danger" disabled={!planReady || executeMutation.isPending} onClick={() => setShowConfirm(true)}>
                {executeMutation.isPending ? 'Executing…' : 'Execute Rebuild'}
              </Button>
            </div>
            {!planReady ? (
              <div className="mt-3 text-sm text-slate-400">Execution unlocks after a successful dry-run plan with no blocking errors.</div>
            ) : null}
          </div>

          {executeMutation.isError ? <div className="mt-4"><ErrorMessage error={executeMutation.error} /></div> : null}

          <div className="mt-5 rounded-md border border-quantum-border bg-quantum-panel p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="font-semibold text-white">Progress / status</h3>
              {detailRun ? <Badge variant={statusVariant(detailRun.status)}>{toTitleCase(detailRun.status)}</Badge> : null}
            </div>
            {!detailRun ? (
              <div className="mt-3 text-sm text-slate-400">No rebuild run selected.</div>
            ) : (
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                <div className="rounded-md border border-quantum-border px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Started</div>
                  <div className="mt-1 text-sm text-slate-200">{formatDate(detailRun.created_at)}</div>
                </div>
                <div className="rounded-md border border-quantum-border px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Completed</div>
                  <div className="mt-1 text-sm text-slate-200">{formatDate(detailRun.completed_at ?? '')}</div>
                </div>
                <div className="rounded-md border border-quantum-border px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Datasets recovered</div>
                  <div className="mt-1 text-sm text-slate-200">{detailRun.datasets_recovered}</div>
                </div>
                <div className="rounded-md border border-quantum-border px-4 py-3">
                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Files recovered</div>
                  <div className="mt-1 text-sm text-slate-200">{detailRun.files_recovered}</div>
                </div>
              </div>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white">Rebuild history</h2>
            <p className="mt-1 text-sm text-slate-400">Refreshes every 10 seconds while a rebuild is active.</p>
          </div>
          <Button variant="secondary" onClick={() => void historyQuery.refetch()}>
            Refresh
          </Button>
        </div>

        {historyQuery.isLoading ? <Spinner /> : null}
        {historyQuery.isError ? <ErrorMessage error={historyQuery.error} onRetry={() => historyQuery.refetch()} /> : null}
        {!historyQuery.isLoading && !historyQuery.isError ? (
          history.length === 0 ? (
            <div className="rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
              No rebuild runs recorded yet.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Run ID</th>
                    <th className="px-4 py-3 font-medium">Status</th>
                    <th className="px-4 py-3 font-medium">Started At</th>
                    <th className="px-4 py-3 font-medium">Completed At</th>
                    <th className="px-4 py-3 font-medium">Datasets Recovered</th>
                    <th className="px-4 py-3 font-medium">Files Recovered</th>
                    <th className="px-4 py-3 font-medium">Errors</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((run, index) => {
                    const expanded = activeRunId === run.id;
                    const rowDetails = expanded ? detailRun ?? run : run;
                    return (
                      <FragmentRow
                        key={run.id}
                        expanded={expanded}
                        rowClassName={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}
                        summary={
                          <tr
                            className={`${index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'} cursor-pointer hover:bg-quantum-sidebar/70`}
                            onClick={() => setActiveRunId((current) => (current === run.id ? null : run.id))}
                          >
                            <td className="px-4 py-3 font-mono text-xs text-slate-200">{run.id}</td>
                            <td className="px-4 py-3"><Badge variant={statusVariant(run.status)}>{toTitleCase(run.status)}</Badge></td>
                            <td className="px-4 py-3 text-slate-300">{formatDate(run.created_at)}</td>
                            <td className="px-4 py-3 text-slate-300">{formatDate(run.completed_at ?? '')}</td>
                            <td className="px-4 py-3 text-slate-300">{run.datasets_recovered}</td>
                            <td className="px-4 py-3 text-slate-300">{run.files_recovered}</td>
                            <td className="px-4 py-3 text-slate-300">{run.error_summary.length}</td>
                          </tr>
                        }
                        details={
                          <tr className="bg-quantum-panel/80">
                            <td colSpan={7} className="px-4 py-4">
                              {activeRunQuery.isLoading && expanded ? <div className="mb-4"><Spinner /></div> : null}
                              {activeRunQuery.isError && expanded ? <div className="mb-4"><ErrorMessage error={activeRunQuery.error} onRetry={() => activeRunQuery.refetch()} /></div> : null}
                              <div className="grid gap-3 lg:grid-cols-4">
                                <div className="rounded-md border border-quantum-border px-4 py-3">
                                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Planned</div>
                                  <div className="mt-2 text-sm text-slate-200">{rowDetails.barcodes_planned.join(', ') || '—'}</div>
                                </div>
                                <div className="rounded-md border border-quantum-border px-4 py-3">
                                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Completed</div>
                                  <div className="mt-2 text-sm text-slate-200">{rowDetails.barcodes_completed.join(', ') || '—'}</div>
                                </div>
                                <div className="rounded-md border border-quantum-border px-4 py-3">
                                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Failed</div>
                                  <div className="mt-2 text-sm text-slate-200">{rowDetails.barcodes_failed.join(', ') || '—'}</div>
                                </div>
                                <div className="rounded-md border border-quantum-border px-4 py-3">
                                  <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Skipped</div>
                                  <div className="mt-2 text-sm text-slate-200">{rowDetails.barcodes_skipped.join(', ') || '—'}</div>
                                </div>
                              </div>
                              <div className="mt-3 rounded-md border border-quantum-border px-4 py-3">
                                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Error summary</div>
                                <div className="mt-2 text-sm text-slate-300">{rowDetails.error_summary.join(' • ') || 'No errors recorded.'}</div>
                              </div>
                            </td>
                          </tr>
                        }
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>

      <ConfirmDialog
        open={showConfirm}
        title="Execute catalog rebuild"
        message="This will modify the catalog. Are you sure?"
        confirmLabel="Execute rebuild"
        isProcessing={executeMutation.isPending}
        onCancel={() => setShowConfirm(false)}
        onConfirm={() => {
          if (latestPlan?.run_id) {
            executeMutation.mutate(latestPlan.run_id);
          }
        }}
      />
    </div>
  );
}

function FragmentRow({
  expanded,
  summary,
  details,
}: {
  expanded: boolean;
  summary: JSX.Element;
  details: JSX.Element;
  rowClassName: string;
}) {
  return (
    <>
      {summary}
      {expanded ? details : null}
    </>
  );
}
