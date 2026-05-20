import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  exportDataset,
  getDataset,
  getDatasetFiles,
  getDatasetManifest,
  getDatasetReport,
  listDatasets,
  listPools,
  verifyDataset,
  type DatasetManifest,
  type DatasetReport,
  type DatasetVerificationResult,
  type NasDataset,
} from '../../api/nas';
import BytesDisplay from '../../components/nas/BytesDisplay';
import ConfirmDialog from '../../components/nas/ConfirmDialog';
import JsonViewerModal from '../../components/nas/JsonViewerModal';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import RestoreRequestModal from '../../components/nas/RestoreRequestModal';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { formatDate } from '../../lib/utils';

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

function shortId(value: string): string {
  return value.length > 10 ? value.slice(0, 10) : value;
}

function truncateChecksum(value: string | null): string {
  if (!value) {
    return '—';
  }
  return value.length > 16 ? `${value.slice(0, 16)}…` : value;
}

function downloadReport(datasetId: string, report: DatasetReport): void {
  const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${datasetId}-report.json`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function VerifyResultModal({
  result,
  onClose,
}: {
  result: DatasetVerificationResult | null;
  onClose: () => void;
}) {
  if (!result) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 py-8">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Verification result</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">Dataset {result.dataset_id}</h2>
          </div>
          <Button type="button" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>

        <div className="mt-6 grid gap-3 md:grid-cols-3">
          <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
            <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Files verified</div>
            <div className="mt-2 text-sm text-slate-100">{result.files_verified}</div>
          </div>
          <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
            <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Files corrupt</div>
            <div className="mt-2 text-sm text-slate-100">{result.files_corrupt}</div>
          </div>
          <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
            <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Checksums updated</div>
            <div className="mt-2 text-sm text-slate-100">{result.files_updated}</div>
          </div>
        </div>

        <div className="mt-6 overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Logical Path</th>
                <th className="px-3 py-3">Checksum</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {Object.entries(result.checksums).map(([path, checksum]) => (
                <tr key={path} className="text-slate-200">
                  <td className="px-3 py-3 font-mono text-xs">{path}</td>
                  <td className="px-3 py-3 font-mono text-xs">{checksum}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function DatasetDetails() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState('');
  const [poolFilter, setPoolFilter] = useState('');
  const [selectedDatasetId, setSelectedDatasetId] = useState<string>('');
  const [expandedTapes, setExpandedTapes] = useState<Record<string, boolean>>({});
  const [focusedTape, setFocusedTape] = useState<string | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [verificationResult, setVerificationResult] = useState<DatasetVerificationResult | null>(null);
  const [manifest, setManifest] = useState<DatasetManifest | null>(null);
  const [datasetToExport, setDatasetToExport] = useState<NasDataset | null>(null);
  const [restoreDataset, setRestoreDataset] = useState<NasDataset | null>(null);

  const poolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listPools });
  const datasetsQuery = useQuery({
    queryKey: ['nas', 'datasets', { statusFilter, poolFilter }],
    queryFn: () => listDatasets({ status: statusFilter || undefined, poolId: poolFilter || undefined }),
  });
  const datasetDetailQuery = useQuery({
    queryKey: ['nas', 'dataset-detail', selectedDatasetId],
    queryFn: () => getDataset(selectedDatasetId),
    enabled: Boolean(selectedDatasetId),
  });
  const datasetFilesQuery = useQuery({
    queryKey: ['nas', 'dataset-files', selectedDatasetId],
    queryFn: () => getDatasetFiles(selectedDatasetId),
    enabled: Boolean(selectedDatasetId),
  });

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  useEffect(() => {
    const datasets = datasetsQuery.data ?? [];
    if (!datasets.length) {
      setSelectedDatasetId('');
      return;
    }
    if (!selectedDatasetId || datasets.every((dataset) => dataset.dataset_id !== selectedDatasetId)) {
      setSelectedDatasetId(datasets[0].dataset_id);
    }
  }, [datasetsQuery.data, selectedDatasetId]);

  const poolNameMap = useMemo(
    () => new Map((poolsQuery.data ?? []).map((pool) => [pool.pool_id, pool.name])),
    [poolsQuery.data],
  );

  const verifyMutation = useMutation({
    mutationFn: (datasetId: string) => verifyDataset(datasetId),
    onSuccess: async (result) => {
      setVerificationResult(result);
      setToast({ type: 'success', message: 'Dataset verification completed.' });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['nas', 'datasets'] }),
        queryClient.invalidateQueries({ queryKey: ['nas', 'dataset-detail', result.dataset_id] }),
        queryClient.invalidateQueries({ queryKey: ['nas', 'dataset-files', result.dataset_id] }),
      ]);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to verify dataset.' });
    },
  });

  const exportMutation = useMutation({
    mutationFn: (datasetId: string) => exportDataset(datasetId),
    onSuccess: async (result) => {
      setToast({ type: 'success', message: 'Dataset marked as exported.' });
      setDatasetToExport(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['nas', 'datasets'] }),
        queryClient.invalidateQueries({ queryKey: ['nas', 'dataset-detail', result.dataset_id] }),
        queryClient.invalidateQueries({ queryKey: ['nas', 'dataset-files', result.dataset_id] }),
      ]);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to export dataset.' });
    },
  });

  const reportMutation = useMutation({
    mutationFn: (datasetId: string) => getDatasetReport(datasetId),
    onSuccess: (report) => {
      downloadReport(report.dataset.dataset_id, report);
      setToast({ type: 'success', message: 'Dataset report downloaded.' });
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to download report.' });
    },
  });

  const manifestMutation = useMutation({
    mutationFn: (datasetId: string) => getDatasetManifest(datasetId),
    onSuccess: (result) => setManifest(result),
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to load manifest.' });
    },
  });

  if (poolsQuery.isLoading || datasetsQuery.isLoading) {
    return <Spinner />;
  }

  if (poolsQuery.isError || datasetsQuery.isError) {
    return <ErrorMessage error={poolsQuery.error ?? datasetsQuery.error} onRetry={() => {
      void poolsQuery.refetch();
      void datasetsQuery.refetch();
    }} />;
  }

  const datasets = datasetsQuery.data ?? [];
  const selectedDataset = datasetDetailQuery.data ?? datasets.find((dataset) => dataset.dataset_id === selectedDatasetId) ?? null;
  const files = datasetFilesQuery.data ?? [];

  return (
    <div className="space-y-4">
      {toast ? (
        <div className={`fixed right-4 top-4 z-50 rounded-md border px-4 py-3 text-sm shadow-lg ${toast.type === 'success' ? 'border-emerald-500/30 bg-emerald-900/90 text-emerald-100' : 'border-red-500/30 bg-red-950/90 text-red-100'}`}>
          {toast.message}
        </div>
      ) : null}

      <Card className="bg-quantum-north">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Dataset Details</h1>
          <p className="mt-2 text-sm text-slate-400">Inspect archived datasets, verify checksums, export metadata, and stage restores.</p>
        </div>
      </Card>

      <Card>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
              <option value="">All statuses</option>
              {['pending', 'archiving', 'archived', 'failed', 'verified', 'exported', 'cancelled'].map((status) => (
                <option key={status} value={status}>{status}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm text-slate-300">
            <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Pool</span>
            <select value={poolFilter} onChange={(event) => setPoolFilter(event.target.value)}>
              <option value="">All pools</option>
              {(poolsQuery.data ?? []).map((pool) => (
                <option key={pool.pool_id} value={pool.pool_id}>{pool.name}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Dataset ID</th>
                <th className="px-3 py-3">Pool</th>
                <th className="px-3 py-3">Policy</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Ingest Mode</th>
                <th className="px-3 py-3">Files</th>
                <th className="px-3 py-3">Total Size</th>
                <th className="px-3 py-3">Tape Set</th>
                <th className="px-3 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {datasets.map((dataset) => (
                <tr key={dataset.dataset_id} className={`cursor-pointer text-slate-200 hover:bg-quantum-sidebar/40 ${selectedDatasetId === dataset.dataset_id ? 'bg-quantum-sidebar/50' : ''}`} onClick={() => setSelectedDatasetId(dataset.dataset_id)}>
                  <td className="px-3 py-3">
                    <div className="font-medium text-slate-100">{shortId(dataset.dataset_id)}</div>
                    <div className="mt-1 text-xs text-slate-500">{dataset.dataset_id}</div>
                  </td>
                  <td className="px-3 py-3">{dataset.pool_id ? poolNameMap.get(dataset.pool_id) ?? dataset.pool_id : '—'}</td>
                  <td className="px-3 py-3">{dataset.policy_name ?? dataset.policy_id ?? '—'}</td>
                  <td className="px-3 py-3"><NasStatusBadge value={dataset.status} /></td>
                  <td className="px-3 py-3"><NasStatusBadge value={dataset.ingest_mode} /></td>
                  <td className="px-3 py-3">{dataset.file_count}</td>
                  <td className="px-3 py-3"><BytesDisplay value={dataset.total_bytes} /></td>
                  <td className="px-3 py-3">{dataset.tape_set.length}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-2" onClick={(event) => event.stopPropagation()}>
                      <Button type="button" variant="secondary" onClick={() => setSelectedDatasetId(dataset.dataset_id)}>View Detail</Button>
                      <Button type="button" variant="secondary" disabled={verifyMutation.isPending} onClick={() => verifyMutation.mutate(dataset.dataset_id)}>Verify</Button>
                      <Button type="button" variant="secondary" disabled={exportMutation.isPending} onClick={() => setDatasetToExport(dataset)}>Export</Button>
                      <Button type="button" variant="secondary" disabled={reportMutation.isPending} onClick={() => reportMutation.mutate(dataset.dataset_id)}>Report</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {datasets.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-400">No datasets match the current filters.</div> : null}
      </Card>

      {datasetDetailQuery.isLoading && selectedDatasetId ? <Spinner /> : null}
      {datasetDetailQuery.isError ? <ErrorMessage error={datasetDetailQuery.error} onRetry={() => void datasetDetailQuery.refetch()} /> : null}

      {selectedDataset ? (
        <Card>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Dataset detail</div>
              <h2 className="mt-1 text-xl font-semibold text-slate-100">{selectedDataset.dataset_id}</h2>
            </div>
            <div className="flex flex-wrap gap-2">
              <NasStatusBadge value={selectedDataset.status} />
              <NasStatusBadge value={selectedDataset.ingest_mode} label={selectedDataset.ingest_mode} />
            </div>
          </div>

          <div className="mt-6 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Pool</div>
              <div className="mt-2 text-sm text-slate-100">{selectedDataset.pool_id ? poolNameMap.get(selectedDataset.pool_id) ?? selectedDataset.pool_id : '—'}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Policy</div>
              <div className="mt-2 text-sm text-slate-100">{selectedDataset.policy_name ?? selectedDataset.policy_id ?? '—'}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Source path</div>
              <div className="mt-2 text-sm text-slate-100">{selectedDataset.source_path ?? '—'}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Created</div>
              <div className="mt-2 text-sm text-slate-100">{selectedDataset.created_at ? formatDate(selectedDataset.created_at) : '—'}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Copies completed</div>
              <div className="mt-2 text-sm text-slate-100">{selectedDataset.copies_completed}</div>
            </div>
          </div>

          <div className="mt-6 flex flex-wrap gap-2">
            <Button type="button" variant="secondary" disabled={verifyMutation.isPending} onClick={() => verifyMutation.mutate(selectedDataset.dataset_id)}>Verify Checksums</Button>
            <Button type="button" variant="secondary" disabled={exportMutation.isPending} onClick={() => setDatasetToExport(selectedDataset)}>Mark as Exported</Button>
            <Button type="button" variant="secondary" disabled={reportMutation.isPending} onClick={() => reportMutation.mutate(selectedDataset.dataset_id)}>Download Report</Button>
            <Button type="button" variant="secondary" disabled={!selectedDataset.pool_id} onClick={() => setRestoreDataset(selectedDataset)}>Request Restore</Button>
            <Button type="button" variant="secondary" disabled={manifestMutation.isPending} onClick={() => manifestMutation.mutate(selectedDataset.dataset_id)}>View Manifest</Button>
          </div>

          <div className="mt-6">
            <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape set</div>
            <div className="mt-3 flex flex-wrap gap-2">
              {selectedDataset.tape_set.length > 0 ? selectedDataset.tape_set.map((tape) => (
                <button
                  key={tape}
                  type="button"
                  className={`rounded-full border px-2.5 py-1 text-xs ${focusedTape === tape ? 'border-purple-500/40 bg-purple-500/15 text-purple-200' : 'border-quantum-border text-slate-200'}`}
                  onClick={() => {
                    setFocusedTape(tape);
                    setExpandedTapes((current) => ({ ...current, [tape]: true }));
                  }}
                >
                  {tape}
                </button>
              )) : <span className="text-sm text-slate-400">No tapes assigned.</span>}
            </div>
          </div>

          <div className="mt-6">
            <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Shard map</div>
            <div className="mt-3 space-y-3">
              {Object.entries(selectedDataset.shard_map).length > 0 ? Object.entries(selectedDataset.shard_map).map(([tape, logicalPaths]) => {
                const open = expandedTapes[tape] ?? focusedTape === tape;
                return (
                  <div key={tape} className={`rounded-md border ${focusedTape === tape ? 'border-purple-500/30' : 'border-quantum-border'} bg-quantum-sidebar`}>
                    <button
                      type="button"
                      className="flex w-full items-center justify-between px-4 py-3 text-left"
                      onClick={() => setExpandedTapes((current) => ({ ...current, [tape]: !open }))}
                    >
                      <span className="text-sm font-medium text-slate-100">{tape}</span>
                      <span className="text-xs text-slate-400">{logicalPaths.length} file(s)</span>
                    </button>
                    {open ? (
                      <div className="border-t border-quantum-border px-4 py-3 text-sm text-slate-300">
                        <ul className="space-y-2 font-mono text-xs">
                          {logicalPaths.map((path) => (
                            <li key={path}>{path}</li>
                          ))}
                        </ul>
                      </div>
                    ) : null}
                  </div>
                );
              }) : <div className="text-sm text-slate-400">No shard map entries available.</div>}
            </div>
          </div>

          <div className="mt-6 overflow-x-auto">
            <table className="min-w-full divide-y divide-quantum-border text-sm">
              <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                <tr>
                  <th className="px-3 py-3">Logical Path</th>
                  <th className="px-3 py-3">Size</th>
                  <th className="px-3 py-3">State</th>
                  <th className="px-3 py-3">Checksum</th>
                  <th className="px-3 py-3">Tape Barcode</th>
                  <th className="px-3 py-3">Modified</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-quantum-border/80">
                {files.map((file) => (
                  <tr key={file.id} className="text-slate-200">
                    <td className="px-3 py-3 font-mono text-xs">{file.relative_path}</td>
                    <td className="px-3 py-3"><BytesDisplay value={file.size_bytes} /></td>
                    <td className="px-3 py-3"><NasStatusBadge value={file.state} /></td>
                    <td className="px-3 py-3 font-mono text-xs">{truncateChecksum(file.checksum_sha256)}</td>
                    <td className="px-3 py-3 font-mono text-xs">{file.tape_barcode ?? '—'}</td>
                    <td className="px-3 py-3">{file.mtime ? formatDate(file.mtime) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {files.length === 0 ? <div className="px-4 py-8 text-center text-sm text-slate-400">No files recorded for this dataset.</div> : null}
          </div>
        </Card>
      ) : null}

      <ConfirmDialog
        open={Boolean(datasetToExport)}
        title="Mark dataset as exported"
        message={datasetToExport ? `Mark ${datasetToExport.dataset_id} as exported? All file states will also be updated.` : ''}
        confirmLabel="Mark exported"
        confirmVariant="danger"
        isProcessing={exportMutation.isPending}
        onCancel={() => setDatasetToExport(null)}
        onConfirm={() => {
          if (datasetToExport) {
            exportMutation.mutate(datasetToExport.dataset_id);
          }
        }}
      />

      <VerifyResultModal result={verificationResult} onClose={() => setVerificationResult(null)} />
      <JsonViewerModal open={Boolean(manifest)} title={manifest ? `Manifest — ${manifest.dataset_id}` : 'Manifest'} data={manifest} onClose={() => setManifest(null)} />

      {restoreDataset ? (
        <RestoreRequestModal
          poolId={restoreDataset.pool_id}
          datasetId={restoreDataset.dataset_id}
          onClose={() => setRestoreDataset(null)}
          onEnqueued={() => {
            setRestoreDataset(null);
            setToast({ type: 'success', message: 'Restore job enqueued.' });
            void queryClient.invalidateQueries({ queryKey: ['nas', 'restore-jobs'] });
          }}
        />
      ) : null}
    </div>
  );
}
