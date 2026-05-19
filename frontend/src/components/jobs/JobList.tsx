import { useMemo, useState } from 'react';
import type { JobResponse } from '../../types/api';
import { formatDate, getJobStatusVariant, toTitleCase } from '../../lib/utils';
import Badge from '../ui/Badge';
import Card from '../ui/Card';

interface JobListProps {
  jobs: JobResponse[];
  selectedId?: string;
  onSelect: (jobId: string) => void;
}

export default function JobList({ jobs, selectedId, onSelect }: JobListProps) {
  const [filter, setFilter] = useState('ALL');
  const [search, setSearch] = useState('');

  const filteredJobs = useMemo(() => {
    return jobs.filter((job) => {
      const matchesFilter = filter === 'ALL' || job.status === filter;
      const haystack = `${job.id} ${job.job_type}`.toLowerCase();
      const matchesSearch = haystack.includes(search.toLowerCase());
      return matchesFilter && matchesSearch;
    });
  }, [filter, jobs, search]);

  return (
    <Card className="h-full">
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">Job Queue</h2>
          <p className="text-sm text-slate-400">Filter by status or search a job id.</p>
        </div>
        <div className="flex gap-2">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search jobs"
            className="w-full min-w-40"
          />
          <select value={filter} onChange={(event) => setFilter(event.target.value)}>
            {['ALL', 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED'].map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border border-slate-800">
        <table className="min-w-full divide-y divide-slate-800 text-sm">
          <thead className="bg-slate-900/80 text-left text-slate-400">
            <tr>
              <th className="px-4 py-3 font-medium">Job</th>
              <th className="px-4 py-3 font-medium">Type</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-blade-900/50">
            {filteredJobs.map((job) => (
              <tr
                key={job.id}
                onClick={() => onSelect(job.id)}
                className={selectedId === job.id ? 'cursor-pointer bg-slate-800/70' : 'cursor-pointer hover:bg-slate-800/40'}
              >
                <td className="px-4 py-3 font-mono text-xs text-slate-200">{job.id.slice(0, 8)}</td>
                <td className="px-4 py-3 text-slate-300">{toTitleCase(job.job_type)}</td>
                <td className="px-4 py-3">
                  <Badge variant={getJobStatusVariant(job.status)}>{job.status}</Badge>
                </td>
                <td className="px-4 py-3 text-slate-400">{formatDate(job.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filteredJobs.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-slate-400">No jobs match the current filters.</div>
        ) : null}
      </div>
    </Card>
  );
}
