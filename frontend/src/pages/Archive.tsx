import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import InformationPanel from '../components/panels/InformationPanel';
import NorthPanel from '../components/panels/NorthPanel';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getArchiveJobs, postArchive } from '../api/archive';
import { getMediaPools } from '../api/cartridges';
import { listCartridges } from '../api/media';
import { listPolicies, listPools as listNasPools } from '../api/nas';
import {
  assignVolumeGroupCartridge,
  createVolumeGroup,
  getVolumeGroups,
} from '../api/volumeGroups';
import { formatDate } from '../lib/utils';
import type { JobResponse, VolumeGroup } from '../types/api';

function stateVariant(state: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  if (state === 'FAILED') return 'red';
  if (state === 'COMPLETED') return 'green';
  if (state === 'RUNNING') return 'blue';
  if (state === 'QUEUED' || state === 'PENDING') return 'amber';
  return 'gray';
}

function progressForJob(job?: JobResponse): number {
  if (!job) return 0;
  if (typeof job.progress === 'number') return job.progress;
  const state = String(job.state ?? job.status).toUpperCase();
  if (state === 'COMPLETED') return 100;
  if (state === 'RUNNING') return 65;
  if (state === 'QUEUED' || state === 'PENDING') return 20;
  return 0;
}

export default function Archive() {
  const queryClient = useQueryClient();
  const archiveJobsQuery = useQuery({ queryKey: ['archive', 'jobs'], queryFn: getArchiveJobs, refetchInterval: 5_000 });
  const volumeGroupsQuery = useQuery({ queryKey: ['volume-groups'], queryFn: getVolumeGroups, refetchInterval: 30_000 });
  const cartridgesQuery = useQuery({ queryKey: ['archive', 'cartridges'], queryFn: listCartridges, refetchInterval: 30_000 });
  const poolsQuery = useQuery({ queryKey: ['archive', 'pools'], queryFn: getMediaPools, refetchInterval: 30_000 });
  const policiesQuery = useQuery({ queryKey: ['nas', 'policies'], queryFn: listPolicies, refetchInterval: 30_000 });
  const nasPoolsQuery = useQuery({ queryKey: ['nas', 'pools'], queryFn: listNasPools, refetchInterval: 30_000 });

  const [sourceDraft, setSourceDraft] = useState('');
  const [sourcePaths, setSourcePaths] = useState<string[]>([]);
  const [volumeGroup, setVolumeGroup] = useState('');
  const [selectedPolicyId, setSelectedPolicyId] = useState('');
  const [selectedPoolId, setSelectedPoolId] = useState('');
  const [preferredTape, setPreferredTape] = useState('');
  const [newGroupName, setNewGroupName] = useState('');
  const [showCreateGroup, setShowCreateGroup] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string>();
  const [submissionSummary, setSubmissionSummary] = useState<string>('');

  const volumeGroups = volumeGroupsQuery.data ?? [];
  const recentJobs = archiveJobsQuery.data ?? [];
  const pools = poolsQuery.data ?? [];
  const policies = policiesQuery.data ?? [];
  const nasPools = nasPoolsQuery.data ?? [];
  const archiveCartridges = useMemo(
    () => (cartridgesQuery.data ?? []).filter((cartridge) => String(cartridge.type ?? '').toUpperCase().startsWith('LTO-')),
    [cartridgesQuery.data],
  );

  useEffect(() => {
    if (!volumeGroup && volumeGroups.length > 0) {
      setVolumeGroup(volumeGroups[0].name);
    }
  }, [volumeGroup, volumeGroups]);

  useEffect(() => {
    if (!selectedJobId && recentJobs.length > 0) {
      setSelectedJobId(recentJobs[0].id);
    }
  }, [recentJobs, selectedJobId]);

  useEffect(() => {
    if (!selectedPolicyId && policies.length > 0) {
      setSelectedPolicyId(policies[0].id);
    }
  }, [policies, selectedPolicyId]);

  const selectedGroup = volumeGroups.find((group) => group.name === volumeGroup) ?? volumeGroups[0];
  const poolOptions = useMemo(
    () => [
      ...pools.map((pool) => ({ id: `aml:${pool.id}`, name: `${pool.name} · Media Pool`, assignedBarcodes: pool.assignedBarcodes })),
      ...nasPools.map((pool) => ({ id: `nas:${pool.pool_id}`, name: `${pool.name} · Virtual Pool`, assignedBarcodes: [] as string[] })),
    ],
    [nasPools, pools],
  );
  const selectedPool = poolOptions.find((pool) => pool.id === selectedPoolId);
  const selectedPolicy = policies.find((policy) => policy.id === selectedPolicyId) ?? policies[0];
  const candidateTapes = useMemo(() => {
    const poolBarcodes = selectedPool && selectedPool.assignedBarcodes.length > 0 ? new Set(selectedPool.assignedBarcodes) : null;
    return archiveCartridges
      .filter((cartridge) => (poolBarcodes ? poolBarcodes.has(cartridge.barcode) : true))
      .sort((left, right) => left.barcode.localeCompare(right.barcode));
  }, [archiveCartridges, selectedPool]);
  const selectedJob = recentJobs.find((job) => job.id === selectedJobId) ?? recentJobs[0];

  const createGroupMutation = useMutation({
    mutationFn: (name: string) => createVolumeGroup(name.trim()),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ['volume-groups'] });
      setVolumeGroup(created.name);
      setNewGroupName('');
      setShowCreateGroup(false);
    },
  });

  const archiveMutation = useMutation({
    mutationFn: async () => {
      const queuedJobIds: string[] = [];
      let currentGroup: VolumeGroup | undefined = selectedGroup;
      if (preferredTape && currentGroup && !(currentGroup.barcodes ?? []).includes(preferredTape)) {
        currentGroup = await assignVolumeGroupCartridge(currentGroup.name, preferredTape);
      }
      for (const sourcePath of sourcePaths) {
        const response = await postArchive({ source_path: sourcePath, volume_group: volumeGroup });
        queuedJobIds.push(response.job_id);
      }
      return queuedJobIds;
    },
    onSuccess: async (jobIds) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['archive', 'jobs'] }),
        queryClient.invalidateQueries({ queryKey: ['volume-groups'] }),
      ]);
      setSubmissionSummary(
        `${jobIds.length} archive job${jobIds.length === 1 ? '' : 's'} queued using ${selectedPolicy?.name ?? 'default policy'}${selectedPool ? ` into ${selectedPool.name}` : ''}.`,
      );
      setSourcePaths([]);
      setSourceDraft('');
      setSelectedJobId(jobIds[0] ?? selectedJobId);
    },
  });

  const queryError = archiveJobsQuery.error ?? volumeGroupsQuery.error ?? cartridgesQuery.error ?? poolsQuery.error ?? policiesQuery.error ?? nasPoolsQuery.error;
  if ([archiveJobsQuery, volumeGroupsQuery, cartridgesQuery, poolsQuery, policiesQuery, nasPoolsQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }
  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={() => {
      void archiveJobsQuery.refetch();
      void volumeGroupsQuery.refetch();
      void cartridgesQuery.refetch();
      void poolsQuery.refetch();
      void policiesQuery.refetch();
      void nasPoolsQuery.refetch();
    }} />;
  }

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Archive</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Archive intake</h1>
            <p className="mt-2 text-sm text-slate-400">
              Queue archive jobs against volume groups, optionally constrain the destination by media pool,
              and pin a preferred tape before submission.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="blue">{volumeGroups.length} groups</Badge>
            <Badge variant="purple">{policies.length} policies</Badge>
            <Badge variant="gray">{poolOptions.length} pools</Badge>
            <Badge variant="green">{archiveCartridges.length} tapes</Badge>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.3fr_0.9fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Queue Builder</div>
              <div className="mt-1 text-lg font-semibold text-slate-100">Source files and destination</div>
            </div>
            <Button variant="secondary" onClick={() => setShowCreateGroup((current) => !current)}>New Group</Button>
          </div>

          {showCreateGroup ? (
            <div className="mt-4 rounded-md border border-quantum-border bg-quantum-sidebar p-4">
              <label className="text-xs uppercase tracking-[0.16em] text-slate-500">Volume Group Name</label>
              <div className="mt-2 flex flex-wrap gap-2">
                <input
                  value={newGroupName}
                  onChange={(event) => setNewGroupName(event.target.value)}
                  placeholder="project-alpha"
                  className="min-w-[220px] flex-1 rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                />
                <Button disabled={!newGroupName.trim() || createGroupMutation.isPending} onClick={() => createGroupMutation.mutate(newGroupName)}>
                  {createGroupMutation.isPending ? 'Creating…' : 'Create'}
                </Button>
              </div>
              {createGroupMutation.isError ? <div className="mt-3"><ErrorMessage error={createGroupMutation.error} /></div> : null}
            </div>
          ) : null}

          <div className="mt-4 space-y-4">
            <div>
              <label className="text-xs uppercase tracking-[0.16em] text-slate-500">Add Source Path</label>
              <div className="mt-2 flex flex-wrap gap-2">
                <input
                  value={sourceDraft}
                  onChange={(event) => setSourceDraft(event.target.value)}
                  placeholder="/data/project-a/file.bin"
                  className="min-w-[260px] flex-1 rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                />
                <Button
                  variant="secondary"
                  disabled={!sourceDraft.trim()}
                  onClick={() => {
                    const value = sourceDraft.trim();
                    if (!value) return;
                    setSourcePaths((current) => (current.includes(value) ? current : [...current, value]));
                    setSourceDraft('');
                  }}
                >
                  Add Source
                </Button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {sourcePaths.length === 0 ? (
                  <div className="rounded-md border border-dashed border-quantum-border px-3 py-3 text-sm text-slate-400">
                    Add one or more source files or directories to archive.
                  </div>
                ) : (
                  sourcePaths.map((path) => (
                    <button
                      key={path}
                      type="button"
                      className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-left text-sm text-slate-200 hover:border-red-400"
                      onClick={() => setSourcePaths((current) => current.filter((item) => item !== path))}
                    >
                      {path}
                    </button>
                  ))
                )}
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-4">
              <label className="block text-sm text-slate-300">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Volume Group</span>
                <select
                  value={volumeGroup}
                  onChange={(event) => setVolumeGroup(event.target.value)}
                  className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                >
                  {volumeGroups.map((group) => (
                    <option key={group.id} value={group.name}>{group.name}</option>
                  ))}
                </select>
              </label>

              <label className="block text-sm text-slate-300">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Storage Policy</span>
                <select
                  value={selectedPolicyId}
                  onChange={(event) => setSelectedPolicyId(event.target.value)}
                  className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                >
                  {policies.map((policy) => (
                    <option key={policy.id} value={policy.id}>{policy.name}</option>
                  ))}
                </select>
              </label>

              <label className="block text-sm text-slate-300">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Media Pool</span>
                <select
                  value={selectedPoolId}
                  onChange={(event) => {
                    setSelectedPoolId(event.target.value);
                    setPreferredTape('');
                  }}
                  className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                >
                  <option value="">Any pool</option>
                  {poolOptions.map((pool) => (
                    <option key={pool.id} value={pool.id}>{pool.name}</option>
                  ))}
                </select>
              </label>

              <label className="block text-sm text-slate-300">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Preferred Tape</span>
                <select
                  value={preferredTape}
                  onChange={(event) => setPreferredTape(event.target.value)}
                  className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none focus:border-quantum-red"
                >
                  <option value="">Auto-select</option>
                  {candidateTapes.map((cartridge) => (
                    <option key={cartridge.barcode} value={cartridge.barcode}>{cartridge.barcode}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-sm text-slate-300">
              <span>
                {selectedGroup ? `${selectedGroup.name} currently includes ${(selectedGroup.barcodes ?? []).length} tape(s).` : 'Select or create a volume group.'}
              </span>
              <Button disabled={archiveMutation.isPending || sourcePaths.length === 0 || !volumeGroup} onClick={() => archiveMutation.mutate()}>
                {archiveMutation.isPending ? 'Queuing…' : 'Queue Archive Jobs'}
              </Button>
            </div>
            {submissionSummary ? <div className="rounded-md border border-emerald-700 bg-emerald-900/20 px-4 py-3 text-sm text-emerald-200">{submissionSummary}</div> : null}
            {archiveMutation.isError ? <ErrorMessage error={archiveMutation.error} /> : null}
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Destination Snapshot</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Selected Group</div>
              <div className="mt-1 text-slate-100">{selectedGroup?.name ?? '—'}</div>
              <div className="mt-2 text-xs text-slate-400">
                {(selectedGroup?.barcodes ?? []).length > 0 ? (selectedGroup?.barcodes ?? []).join(', ') : 'No tapes assigned yet'}
              </div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Storage Policy</div>
              <div className="mt-1 text-slate-100">{selectedPolicy?.name ?? 'Default policy'}</div>
              <div className="mt-2 text-xs text-slate-400">NAS policies are shared with Storage pages.</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Pool Filter</div>
              <div className="mt-1 text-slate-100">{selectedPool?.name ?? 'Any pool'}</div>
              <div className="mt-2 text-xs text-slate-400">{candidateTapes.length} eligible archive tape(s)</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Tape Target</div>
              <div className="mt-1 text-slate-100">{preferredTape || 'Automatic from volume group'}</div>
            </div>
          </div>
        </Card>
      </div>

      <NorthPanel
        title="Recent Archive Jobs"
        subtitle="Jobs queued through the root /archive endpoint and tracked in the catalog job table."
        columns={[
          { key: 'id', header: 'Job ID', render: (row: JobResponse) => <span className="font-mono text-xs">{row.id}</span> },
          { key: 'source', header: 'Source', render: (row: JobResponse) => String(row.metadata?.source_path ?? '—') },
          { key: 'group', header: 'Volume Group', render: (row: JobResponse) => String(row.metadata?.volume_group ?? '—') },
          {
            key: 'state',
            header: 'State',
            render: (row: JobResponse) => <Badge variant={stateVariant(String(row.state ?? row.status).toUpperCase())}>{String(row.state ?? row.status).toUpperCase()}</Badge>,
          },
          {
            key: 'progress',
            header: 'Progress',
            render: (row: JobResponse) => `${progressForJob(row)}%`,
          },
          { key: 'updated', header: 'Updated', render: (row: JobResponse) => formatDate(row.updated_at) },
        ]}
        rows={recentJobs}
        getRowId={(row) => row.id}
        selectedId={selectedJob?.id}
        onSelect={(row) => setSelectedJobId(row.id)}
        emptyMessage="No archive jobs have been queued yet."
      />

      <InformationPanel
        title={selectedJob ? `Archive Job ${selectedJob.id}` : 'Archive Guidance'}
        subtitle="Track queue state, metadata, and the selected destination context."
        items={[
          { label: 'State', value: selectedJob ? String(selectedJob.state ?? selectedJob.status).toUpperCase() : 'Ready' },
          { label: 'Progress', value: `${progressForJob(selectedJob)}%` },
          { label: 'Source', value: selectedJob ? String(selectedJob.metadata?.source_path ?? '—') : sourcePaths.join(', ') || '—' },
          { label: 'Volume Group', value: selectedJob ? String(selectedJob.metadata?.volume_group ?? '—') : selectedGroup?.name ?? '—' },
          { label: 'Storage Policy', value: selectedPolicy?.name ?? 'Default' },
          { label: 'Preferred Tape', value: preferredTape || 'Automatic' },
          { label: 'Updated', value: selectedJob ? formatDate(selectedJob.updated_at) : 'Waiting for submission' },
        ]}
      />
    </div>
  );
}
