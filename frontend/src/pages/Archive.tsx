import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { postArchive } from '../api/archive';
import { rootApiRequest } from '../api/client';
import { getVolumeGroups } from '../api/volumeGroups';
import { useJobs } from '../hooks/useJobs';
import { getJobBarcode, getJobState, getJobStrategy, getJobTypeLabel } from '../lib/lmc';
import { formatDate } from '../lib/utils';
import type { JobResponse, VolumeGroup } from '../types/api';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (state === 'FAILED') return 'red';
  if (state === 'RUNNING') return 'blue';
  if (state === 'COMPLETED') return 'green';
  return 'amber';
}

export default function Archive() {
  const queryClient = useQueryClient();
  const jobsQuery = useJobs();
  const volumeGroupsQuery = useQuery({ queryKey: ['volume-groups'], queryFn: getVolumeGroups, refetchInterval: 30_000 });

  const [sourcePath, setSourcePath] = useState('');
  const [volumeGroup, setVolumeGroup] = useState('');
  const [newGroupName, setNewGroupName] = useState('');
  const [selectedJobId, setSelectedJobId] = useState<string>();
  const [showCreateGroup, setShowCreateGroup] = useState(false);

  const volumeGroups: VolumeGroup[] = volumeGroupsQuery.data ?? [];

  // Auto-select first volume group when loaded
  useEffect(() => {
    if (!volumeGroup && volumeGroups.length > 0) {
      setVolumeGroup(volumeGroups[0].name);
    }
  }, [volumeGroup, volumeGroups]);

  const archiveMutation = useMutation({
    mutationFn: (payload: { source_path: string; volume_group: string }) => postArchive(payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['jobs'] });
      setSourcePath('');
    },
  });

  const createGroupMutation = useMutation({
    mutationFn: (name: string) =>
      rootApiRequest<VolumeGroup>('/volume-groups/', { method: 'POST', body: { name } }),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['volume-groups'] });
      setVolumeGroup(created.name);
      setNewGroupName('');
      setShowCreateGroup(false);
    },
  });

  const jobs = jobsQuery.data ?? [];
  const recentArchives = useMemo(
    () => jobs.filter((job) => getJobTypeLabel(job).toLowerCase().includes('archive')),
    [jobs],
  );

  useEffect(() => {
    if (!selectedJobId && recentArchives.length > 0) {
      setSelectedJobId(recentArchives[0].id);
    }
  }, [recentArchives, selectedJobId]);

  const selectedJob = recentArchives.find((job) => job.id === selectedJobId) ?? recentArchives[0];
  const selectedGroupDetail = volumeGroups.find((g) => g.name === volumeGroup);

  if (jobsQuery.isLoading || volumeGroupsQuery.isLoading) return <Spinner />;
  if (jobsQuery.isError) return <ErrorMessage error={jobsQuery.error} onRetry={() => jobsQuery.refetch()} />;

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-info">
        <div className="border-b border-quantum-border pb-3">
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Operations Panel</div>
          <h2 className="mt-1 text-lg font-semibold text-slate-100">Archive Operation</h2>
          <p className="mt-1 text-sm text-slate-400">
            Submit an archive job to a volume group. Volume groups collect one or more cartridges into a named logical pool.
          </p>
        </div>

        {/* Create volume group inline */}
        {showCreateGroup ? (
          <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
            <p className="mb-3 text-sm font-medium text-slate-300">Create New Volume Group</p>
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Group Name</label>
                <input
                  value={newGroupName}
                  onChange={(e) => setNewGroupName(e.target.value)}
                  placeholder="e.g. project-alpha"
                />
              </div>
              <Button
                variant="primary"
                disabled={!newGroupName || createGroupMutation.isPending}
                onClick={() => createGroupMutation.mutate(newGroupName)}
              >
                {createGroupMutation.isPending ? 'Creating…' : 'Create'}
              </Button>
              <Button variant="ghost" onClick={() => setShowCreateGroup(false)}>Cancel</Button>
            </div>
            {createGroupMutation.isError ? (
              <div className="mt-3"><ErrorMessage error={createGroupMutation.error} /></div>
            ) : null}
          </div>
        ) : null}

        <form
          className="mt-4 grid gap-4 xl:grid-cols-[1.4fr,1fr,auto] xl:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            archiveMutation.mutate({ source_path: sourcePath, volume_group: volumeGroup });
          }}
        >
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-500">Source Path</label>
            <input
              value={sourcePath}
              onChange={(e) => setSourcePath(e.target.value)}
              placeholder="/data/project-a"
              required
            />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="block text-xs uppercase tracking-wide text-slate-500">Volume Group</label>
              <button
                type="button"
                className="text-xs text-quantum-red hover:underline"
                onClick={() => setShowCreateGroup((v) => !v)}
              >
                + New Group
              </button>
            </div>
            {volumeGroups.length > 0 ? (
              <select value={volumeGroup} onChange={(e) => setVolumeGroup(e.target.value)} required>
                {volumeGroups.map((g) => (
                  <option key={g.id} value={g.name}>
                    {g.name} ({(g.barcodes ?? []).length} cartridge{(g.barcodes ?? []).length === 1 ? '' : 's'})
                  </option>
                ))}
              </select>
            ) : (
              <div className="rounded-md border border-amber-500/30 bg-amber-900/10 px-3 py-2 text-sm text-amber-300">
                No volume groups. Click <strong>+ New Group</strong> to create one first.
              </div>
            )}
          </div>

          <Button
            type="submit"
            variant="primary"
            disabled={archiveMutation.isPending || !sourcePath || !volumeGroup || volumeGroups.length === 0}
          >
            {archiveMutation.isPending ? 'Submitting…' : 'Submit'}
          </Button>
        </form>

        {archiveMutation.isError ? <div className="mt-4"><ErrorMessage error={archiveMutation.error} /></div> : null}
        {archiveMutation.isSuccess ? (
          <div className="mt-4 rounded-md border border-emerald-700 bg-emerald-900/20 px-3 py-3 text-sm text-emerald-200">
            ✓ Archive request queued successfully.
          </div>
        ) : null}
      </Card>

      {/* Volume groups summary */}
      {volumeGroups.length > 0 ? (
        <Card className="bg-quantum-north">
          <div className="mb-3 text-xs uppercase tracking-[0.26em] text-slate-500">Volume Groups</div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {volumeGroups.map((g) => (
              <button
                key={g.id}
                type="button"
                onClick={() => setVolumeGroup(g.name)}
                className={`rounded-md border px-3 py-3 text-left transition ${
                  volumeGroup === g.name
                    ? 'border-quantum-selected-border bg-quantum-selected'
                    : 'border-quantum-border bg-quantum-panel hover:bg-quantum-info'
                }`}
              >
                <div className="text-sm font-semibold text-slate-100">{g.name}</div>
                <div className="mt-1 text-xs text-slate-400">
                  {(g.barcodes ?? []).length} cartridge{(g.barcodes ?? []).length === 1 ? '' : 's'}
                  {(g.barcodes ?? []).length > 0 ? `: ${(g.barcodes ?? []).slice(0, 3).join(', ')}${(g.barcodes ?? []).length > 3 ? '…' : ''}` : ''}
                </div>
              </button>
            ))}
          </div>
        </Card>
      ) : null}

      <NorthPanel
        title="Recent Archives"
        subtitle="Archive jobs submitted through the LMC."
        columns={[
          { key: 'id', header: 'Job ID', render: (row: JobResponse) => <span className="font-mono text-xs">{row.id}</span> },
          { key: 'state', header: 'State', render: (row: JobResponse) => <Badge variant={stateVariant(getJobState(row))}>{getJobState(row)}</Badge> },
          { key: 'barcode', header: 'Barcode', render: (row: JobResponse) => getJobBarcode(row) },
          { key: 'strategy', header: 'Strategy', render: (row: JobResponse) => getJobStrategy(row) },
          { key: 'started', header: 'Started', render: (row: JobResponse) => formatDate(row.created_at) },
        ]}
        rows={recentArchives}
        getRowId={(row) => row.id}
        selectedId={selectedJob?.id}
        onSelect={(row) => setSelectedJobId(row.id)}
        emptyMessage="No archive jobs yet. Submit one above."
      />

      <InformationPanel
        title={selectedJob ? `Archive Job ${selectedJob.id}` : 'Archive Guidance'}
        subtitle="Selected job details or current form state."
        items={[
          { label: 'Volume Group', value: selectedJob ? getJobBarcode(selectedJob) : volumeGroup || '—' },
          { label: 'Cartridges', value: selectedGroupDetail ? (selectedGroupDetail.barcodes ?? []).join(', ') || 'None assigned' : '—' },
          { label: 'Strategy', value: selectedJob ? getJobStrategy(selectedJob) : 'Single Drive' },
          { label: 'State', value: selectedJob ? getJobState(selectedJob) : 'Ready' },
          { label: 'Source Path', value: selectedJob?.metadata?.source_path ? String(selectedJob.metadata.source_path) : sourcePath || '—' },
        ]}
      />
    </div>
  );
}
