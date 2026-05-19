import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { postArchive } from '../../api/archive';
import type { VolumeGroup } from '../../types/api';
import Button from '../ui/Button';
import Card from '../ui/Card';
import ErrorMessage from '../ui/ErrorMessage';

interface ArchiveFormProps {
  volumeGroups: VolumeGroup[];
}

export default function ArchiveForm({ volumeGroups }: ArchiveFormProps) {
  const [sourcePath, setSourcePath] = useState('');
  const [volumeGroup, setVolumeGroup] = useState(volumeGroups[0]?.name ?? '');
  const mutation = useMutation({
    mutationFn: postArchive,
  });

  return (
    <Card className="space-y-4">
      <div>
        <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Standard</p>
        <h2 className="mt-1 text-xl font-semibold text-white">Single-tape archive</h2>
        <p className="mt-1 text-sm text-slate-400">Queue a straightforward archive job to one volume group.</p>
      </div>
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate({ source_path: sourcePath, volume_group: volumeGroup });
        }}
      >
        <div className="space-y-2">
          <label className="text-sm font-medium text-slate-300">Source path</label>
          <input value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} placeholder="/data/archive" required />
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
        <Button type="submit" disabled={mutation.isPending || volumeGroups.length === 0}>
          {mutation.isPending ? 'Queueing…' : 'Start archive'}
        </Button>
      </form>
      {mutation.isSuccess ? (
        <p className="rounded-xl border border-emerald-500/20 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
          Archive job queued: {mutation.data.job_id}
        </p>
      ) : null}
      {mutation.isError ? <ErrorMessage error={mutation.error} /> : null}
    </Card>
  );
}
