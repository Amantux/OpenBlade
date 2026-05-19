import type { JobResponse } from '../../types/api';
import { formatDate, getJobStatusVariant, getMetadataString, toTitleCase } from '../../lib/utils';
import Badge from '../ui/Badge';
import Card from '../ui/Card';

interface JobCardProps {
  job: JobResponse;
}

export default function JobCard({ job }: JobCardProps) {
  return (
    <Card className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Selected Job</p>
          <h2 className="mt-1 text-xl font-semibold text-white">{toTitleCase(job.job_type ?? job.type ?? 'archive')}</h2>
          <p className="mt-1 font-mono text-xs text-slate-400">{job.id}</p>
        </div>
        <Badge variant={getJobStatusVariant(job.status)}>{job.status}</Badge>
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Created</p>
          <p className="mt-1 text-sm text-slate-200">{formatDate(job.created_at)}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Updated</p>
          <p className="mt-1 text-sm text-slate-200">{formatDate(job.updated_at)}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Source</p>
          <p className="mt-1 text-sm text-slate-200">{getMetadataString(job, 'source_path') ?? getMetadataString(job, 'catalog_path') ?? '—'}</p>
        </div>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">Volume Group</p>
          <p className="mt-1 text-sm text-slate-200">{getMetadataString(job, 'volume_group') ?? '—'}</p>
        </div>
      </div>
      {job.error ? <p className="rounded-xl border border-red-500/20 bg-red-950/20 px-4 py-3 text-sm text-red-200">{job.error}</p> : null}
    </Card>
  );
}
