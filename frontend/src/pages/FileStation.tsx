import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, CheckCircle2, Download, FolderArchive, RefreshCw, Trash2, Upload } from 'lucide-react';
import { getFileChecksum, listFiles, removeFile, uploadToPool, downloadFile, type UploadedFile } from '../api/filestation';
import { listPools, type NasPool } from '../api/nas';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { cn, formatBytes, formatDate, toTitleCase, type BadgeVariant } from '../lib/utils';

interface UploadSummary {
  file: UploadedFile;
  checksumVerified: boolean;
}

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

function statusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'pending_archive':
      return 'amber';
    case 'archived':
      return 'green';
    case 'hydrating':
      return 'blue';
    case 'failed':
      return 'red';
    default:
      return 'gray';
  }
}

function FileStatusBadge({ status }: { status: string }) {
  return <Badge variant={statusVariant(status)}>{toTitleCase(status)}</Badge>;
}

function PoolSelect({ pools, selectedPoolId, onSelect }: { pools: NasPool[]; selectedPoolId: string; onSelect: (poolId: string) => void }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Pool</span>
      <select
        value={selectedPoolId}
        onChange={(event) => onSelect(event.target.value)}
        className="w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
      >
        {pools.map((pool) => (
          <option key={pool.pool_id} value={pool.pool_id}>{pool.name}</option>
        ))}
      </select>
    </label>
  );
}

export default function FileStation() {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [selectedPoolId, setSelectedPoolId] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [lastUpload, setLastUpload] = useState<UploadSummary | null>(null);

  const poolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listPools, refetchInterval: 30_000 });
  const effectiveSelectedPoolId = useMemo(() => {
    const pools = poolsQuery.data ?? [];
    if (!pools.length) {
      return '';
    }
    return pools.some((pool) => pool.pool_id === selectedPoolId) ? selectedPoolId : pools[0].pool_id;
  }, [poolsQuery.data, selectedPoolId]);
  const filesQuery = useQuery({
    queryKey: ['file-station', 'files', effectiveSelectedPoolId],
    queryFn: () => listFiles(effectiveSelectedPoolId),
    enabled: Boolean(effectiveSelectedPoolId),
    refetchInterval: 15_000,
  });

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      if (!effectiveSelectedPoolId) {
        throw new Error('Select a pool before uploading.');
      }
      setUploadProgress(0);
      const uploaded = await uploadToPool(effectiveSelectedPoolId, file, (pct) => setUploadProgress(pct));
      const checksum = await getFileChecksum(uploaded.file_id);
      return {
        file: uploaded,
        checksumVerified: checksum.checksum_sha256 === uploaded.checksum_sha256,
      } satisfies UploadSummary;
    },
    onSuccess: async (summary) => {
      setLastUpload(summary);
      setToast({
        type: summary.checksumVerified ? 'success' : 'error',
        message: summary.checksumVerified ? `${summary.file.filename} uploaded and checksum verified.` : `${summary.file.filename} uploaded but checksum verification failed.`,
      });
      await queryClient.invalidateQueries({ queryKey: ['file-station', 'files', effectiveSelectedPoolId] });
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Upload failed.' });
    },
    onSettled: () => {
      setUploadProgress(null);
      if (inputRef.current) {
        inputRef.current.value = '';
      }
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => removeFile(fileId),
    onSuccess: async () => {
      setToast({ type: 'success', message: 'Staged file removed.' });
      await queryClient.invalidateQueries({ queryKey: ['file-station', 'files', effectiveSelectedPoolId] });
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to remove file.' });
    },
  });

  const fileRows = useMemo(() => filesQuery.data?.files ?? [], [filesQuery.data?.files]);
  const totalBytes = useMemo(() => fileRows.reduce((sum, file) => sum + file.size_bytes, 0), [fileRows]);

  const queryError = poolsQuery.error ?? filesQuery.error;
  if (poolsQuery.isLoading && !poolsQuery.data) {
    return <Spinner />;
  }
  if (queryError && !poolsQuery.data) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void poolsQuery.refetch();
      void filesQuery.refetch();
    }} />;
  }

  const hasPools = (poolsQuery.data ?? []).length > 0;

  function handlePickedFile(file: File | null | undefined) {
    if (!file) {
      return;
    }
    uploadMutation.mutate(file);
  }

  function handleInputChange(event: ChangeEvent<HTMLInputElement>) {
    handlePickedFile(event.target.files?.[0]);
  }

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    setIsDragging(false);
    handlePickedFile(event.dataTransfer.files?.[0]);
  }

  async function handleDownload(file: UploadedFile) {
    try {
      await downloadFile(file.file_id, file.filename);
    } catch (error) {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Download failed.' });
    }
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Files</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">File Station</h1>
            <p className="mt-2 text-sm text-slate-400">Upload files into a pool staging inbox, verify SHA-256 checksums, and download or remove staged content.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="blue">{fileRows.length} staged</Badge>
            <Badge variant="gray">{formatBytes(totalBytes)}</Badge>
            {uploadProgress !== null ? <Badge variant="amber">{uploadProgress}% uploading</Badge> : null}
          </div>
        </div>
      </Card>

      {toast ? (
        <div className={cn('flex items-center gap-2 rounded-md border px-4 py-3 text-sm', toast.type === 'success' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-red-500/30 bg-red-500/10 text-red-200')}>
          {toast.type === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
          <span>{toast.message}</span>
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Upload</div>
              <h2 className="mt-1 text-lg font-semibold text-white">Pool staging inbox</h2>
            </div>
            <Button variant="secondary" onClick={() => inputRef.current?.click()} disabled={!hasPools || uploadMutation.isPending}>
              <Upload className="mr-2 h-4 w-4" />
              Select File
            </Button>
          </div>

          <div className="mt-4 space-y-4">
            <PoolSelect pools={poolsQuery.data ?? []} selectedPoolId={effectiveSelectedPoolId} onSelect={setSelectedPoolId} />
            <input ref={inputRef} type="file" className="hidden" onChange={handleInputChange} />
            <button
              type="button"
              onClick={() => inputRef.current?.click()}
              onDragOver={(event) => {
                event.preventDefault();
                setIsDragging(true);
              }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleDrop}
              disabled={!hasPools || uploadMutation.isPending}
              className={cn(
                'flex min-h-44 w-full flex-col items-center justify-center rounded-xl border border-dashed px-6 py-8 text-center transition',
                isDragging ? 'border-quantum-red bg-quantum-north' : 'border-quantum-border bg-quantum-sidebar',
                (!hasPools || uploadMutation.isPending) && 'cursor-not-allowed opacity-70',
              )}
            >
              <FolderArchive className="h-8 w-8 text-slate-300" />
              <div className="mt-4 text-base font-medium text-white">Drag a file here or click to upload</div>
              <div className="mt-2 text-sm text-slate-400">
                {hasPools ? 'The browser computes a SHA-256 checksum and the server validates it while staging.' : 'No NAS pools are configured yet. Create one on the Virtual Pools page.'}
              </div>
              {uploadProgress !== null ? (
                <div className="mt-4 w-full max-w-md">
                  <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.16em] text-slate-500">
                    <span>Progress</span>
                    <span>{uploadProgress}%</span>
                  </div>
                  <div className="h-3 overflow-hidden rounded-full bg-slate-900/80">
                    <div className="h-full rounded-full bg-quantum-red transition-all" style={{ width: `${uploadProgress}%` }} />
                  </div>
                </div>
              ) : null}
            </button>
          </div>
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Verification</div>
              <h2 className="mt-1 text-lg font-semibold text-white">Latest upload</h2>
            </div>
            <Button variant="ghost" onClick={() => {
              void poolsQuery.refetch();
              void filesQuery.refetch();
            }}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
          </div>

          {lastUpload ? (
            <div className="mt-4 space-y-4 rounded-lg border border-quantum-border bg-quantum-sidebar p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-white">{lastUpload.file.filename}</div>
                  <div className="mt-1 text-sm text-slate-400">{formatBytes(lastUpload.file.size_bytes)} • Pool {lastUpload.file.pool_id ?? '—'}</div>
                </div>
                <FileStatusBadge status={lastUpload.file.status} />
              </div>
              <div>
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Checksum</div>
                <div className="mt-2 break-all font-mono text-xs text-slate-200">{lastUpload.file.checksum_sha256}</div>
              </div>
              <div className={cn('flex items-center gap-2 rounded-md px-3 py-2 text-sm', lastUpload.checksumVerified ? 'bg-emerald-500/10 text-emerald-200' : 'bg-red-500/10 text-red-200')}>
                {lastUpload.checksumVerified ? <CheckCircle2 className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                <span>{lastUpload.checksumVerified ? 'Checksum verified after upload.' : 'Checksum verification mismatch detected.'}</span>
              </div>
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-dashed border-quantum-border px-4 py-8 text-sm text-slate-400">
              Upload a file to populate staging metadata and checksum verification details.
            </div>
          )}
        </Card>
      </div>

      <Card>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Staging files</div>
            <h2 className="mt-1 text-lg font-semibold text-white">Selected pool contents</h2>
            <p className="mt-1 text-sm text-slate-400">Review filenames, checksum state, and download or remove staged files before archive processing.</p>
          </div>
          {hasPools ? <PoolSelect pools={poolsQuery.data ?? []} selectedPoolId={effectiveSelectedPoolId} onSelect={setSelectedPoolId} /> : null}
        </div>

        {filesQuery.isLoading && !filesQuery.data ? <div className="mt-6"><Spinner /></div> : null}
        {filesQuery.isError ? <div className="mt-6"><ErrorMessage error={filesQuery.error} onRetry={() => filesQuery.refetch()} /></div> : null}

        {!filesQuery.isLoading && !filesQuery.isError ? (
          fileRows.length > 0 ? (
            <div className="mt-6 overflow-x-auto">
              <table className="min-w-full divide-y divide-quantum-border text-left text-sm">
                <thead>
                  <tr className="text-xs uppercase tracking-[0.16em] text-slate-500">
                    <th className="px-3 py-3">Filename</th>
                    <th className="px-3 py-3">Size</th>
                    <th className="px-3 py-3">Status</th>
                    <th className="px-3 py-3">Checksum</th>
                    <th className="px-3 py-3">Created</th>
                    <th className="px-3 py-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-quantum-border/70">
                  {fileRows.map((file) => (
                    <tr key={file.file_id} className="hover:bg-quantum-sidebar/40">
                      <td className="px-3 py-3 text-slate-100">
                        <div className="font-medium">{file.filename}</div>
                        <div className="mt-1 font-mono text-xs text-slate-500">{file.file_id}</div>
                      </td>
                      <td className="px-3 py-3 text-slate-300">{formatBytes(file.size_bytes)}</td>
                      <td className="px-3 py-3"><FileStatusBadge status={file.status} /></td>
                      <td className="px-3 py-3 font-mono text-xs text-slate-300">{file.checksum_sha256 ?? '—'}</td>
                      <td className="px-3 py-3 text-slate-400">{file.created_at ? formatDate(file.created_at) : '—'}</td>
                      <td className="px-3 py-3">
                        <div className="flex justify-end gap-2">
                          <Button type="button" variant="secondary" onClick={() => void handleDownload(file)}>
                            <Download className="mr-2 h-4 w-4" />
                            Download
                          </Button>
                          <Button type="button" variant="danger" disabled={deleteMutation.isPending} onClick={() => deleteMutation.mutate(file.file_id)}>
                            <Trash2 className="mr-2 h-4 w-4" />
                            Remove
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mt-6 rounded-lg border border-dashed border-quantum-border px-4 py-8 text-sm text-slate-400">
              {hasPools ? 'No staged files are present for the selected pool.' : 'No pools are available for File Station yet.'}
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}
