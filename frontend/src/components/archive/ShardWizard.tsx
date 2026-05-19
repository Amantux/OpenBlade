import { useEffect, useMemo, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { postArchive, postShardedArchive } from '../../api/archive';
import type { DriveResponse, VolumeGroup } from '../../types/api';
import { cn } from '../../lib/utils';
import Button from '../ui/Button';
import Card from '../ui/Card';
import ErrorMessage from '../ui/ErrorMessage';

type Profile = 'STANDARD' | 'STRIPE' | 'BLOCK_STRIPE';

interface ShardWizardProps {
  volumeGroups: VolumeGroup[];
  drives: DriveResponse[];
}

const profiles: Array<{ key: Profile; label: string; description: string }> = [
  { key: 'STANDARD', label: 'Standard', description: 'Single tape workflow for straightforward archive jobs.' },
  { key: 'STRIPE', label: 'Balanced throughput', description: 'Parallel stripe files across multiple loaded tapes.' },
  { key: 'BLOCK_STRIPE', label: 'Large sequential archive', description: 'Split large streams across loaded tapes with a tunable block size.' },
];

export default function ShardWizard({ volumeGroups, drives }: ShardWizardProps) {
  const loadedDrives = useMemo(() => drives.filter((drive) => drive.loaded && drive.barcode), [drives]);
  const [profile, setProfile] = useState<Profile>('STANDARD');
  const [sourcePath, setSourcePath] = useState('');
  const [volumeGroup, setVolumeGroup] = useState(volumeGroups[0]?.name ?? '');
  const [selectedBarcodes, setSelectedBarcodes] = useState<string[]>([]);
  const [blockSizeMb, setBlockSizeMb] = useState(128);

  useEffect(() => {
    if (!volumeGroup && volumeGroups.length > 0) {
      setVolumeGroup(volumeGroups[0].name);
    }
  }, [volumeGroup, volumeGroups]);

  const mutation = useMutation({
    mutationFn: async () => {
      if (profile === 'STANDARD') {
        return postArchive({ source_path: sourcePath, volume_group: volumeGroup });
      }
      return postShardedArchive({
        source_path: sourcePath,
        volume_group: volumeGroup,
        lane_barcodes: selectedBarcodes,
        mode: profile === 'BLOCK_STRIPE' ? 'BLOCK_STRIPE' : 'STRIPE',
        block_size_mb: blockSizeMb,
      });
    },
  });

  function toggleBarcode(barcode: string) {
    setSelectedBarcodes((current) =>
      current.includes(barcode) ? current.filter((item) => item !== barcode) : [...current, barcode],
    );
  }

  const needsLaneSelection = profile !== 'STANDARD';
  const canSubmit = !needsLaneSelection || selectedBarcodes.length > 0;

  return (
    <Card className="space-y-5">
      <div>
        <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Profiles</p>
        <h2 className="mt-1 text-xl font-semibold text-white">Shard planning wizard</h2>
        <p className="mt-1 text-sm text-slate-400">Choose an operator-friendly profile or drop into advanced block striping.</p>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {profiles.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setProfile(item.key)}
            className={cn(
              'rounded-2xl border px-4 py-4 text-left transition',
              profile === item.key
                ? 'border-blue-500 bg-blue-500/10'
                : 'border-slate-800 bg-slate-900/60 hover:border-slate-700',
            )}
          >
            <p className="text-sm font-semibold text-white">{item.label}</p>
            <p className="mt-2 text-sm text-slate-400">{item.description}</p>
          </button>
        ))}
      </div>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (canSubmit) {
            mutation.mutate();
          }
        }}
      >
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-300">Source path</label>
            <input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="/data/large-dataset" required />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-slate-300">Volume group</label>
            <select value={volumeGroup} onChange={(event) => setVolumeGroup(event.target.value)} required>
              {volumeGroups.map((group) => (
                <option key={group.id} value={group.name}>
                  {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>
        {needsLaneSelection ? (
          <div className="space-y-3">
            <p className="text-sm font-medium text-slate-300">Loaded drive lanes</p>
            <div className="grid gap-3 md:grid-cols-2">
              {loadedDrives.map((drive) => (
                <label key={drive.drive_id} className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900/50 px-4 py-3 text-sm text-slate-200">
                  <input
                    type="checkbox"
                    checked={selectedBarcodes.includes(drive.barcode ?? '')}
                    onChange={() => toggleBarcode(drive.barcode ?? '')}
                  />
                  <span>Drive {drive.drive_id}</span>
                  <span className="font-mono text-xs text-slate-400">{drive.barcode}</span>
                </label>
              ))}
            </div>
            {loadedDrives.length === 0 ? <p className="text-sm text-amber-300">No loaded drives are available for striping.</p> : null}
          </div>
        ) : null}
        {profile === 'BLOCK_STRIPE' ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm text-slate-300">
              <label htmlFor="block-size">Block size</label>
              <span>{blockSizeMb} MB</span>
            </div>
            <input
              id="block-size"
              type="range"
              min="16"
              max="512"
              step="16"
              value={blockSizeMb}
              onChange={(event) => setBlockSizeMb(Number(event.target.value))}
            />
          </div>
        ) : null}
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={mutation.isPending || volumeGroups.length === 0 || !canSubmit}>
            {mutation.isPending ? 'Queueing…' : 'Launch workflow'}
          </Button>
          <span className="text-sm text-slate-400">
            {profile === 'BLOCK_STRIPE' ? 'Advanced' : profile === 'STRIPE' ? 'Balanced throughput' : 'Single-tape'}
          </span>
        </div>
      </form>
      {mutation.isSuccess ? (
        <p className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
          Workflow queued: {mutation.data.job_id}
        </p>
      ) : null}
      {mutation.isError ? <ErrorMessage error={mutation.error} /> : null}
    </Card>
  );
}
