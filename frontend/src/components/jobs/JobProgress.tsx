import { CheckCircle2 } from 'lucide-react';
import type { JobResponse } from '../../types/api';
import { formatBytes, getJobPhase, getMetadataNumber, getThroughput } from '../../lib/utils';
import Card from '../ui/Card';

const phases = ['Pending', 'Preparing', 'Mounting', 'Writing', 'Verifying', 'Finalizing', 'Done'];

interface JobProgressProps {
  job: JobResponse;
}

export default function JobProgress({ job }: JobProgressProps) {
  const currentPhase = getJobPhase(job);
  const phaseIndex = Math.max(phases.findIndex((phase) => phase === currentPhase), 0);
  const bytesWritten = job.bytes_written ?? getMetadataNumber(job, 'bytes_written') ?? 0;
  const throughput = getThroughput(job);
  const progress = Math.round((phaseIndex / (phases.length - 1)) * 100);

  return (
    <Card className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-white">Phase Tracker</h3>
          <p className="text-sm text-slate-400">Current phase: {currentPhase}</p>
        </div>
        {throughput ? <p className="text-sm font-semibold text-emerald-300">{throughput}</p> : null}
      </div>
      <div className="grid gap-4 md:grid-cols-7">
        {phases.map((phase, index) => {
          const completed = index < phaseIndex;
          const active = index === phaseIndex;
          return (
            <div key={phase} className="flex items-center gap-3 md:flex-col md:items-start">
              <div
                className={active ? 'flex h-9 w-9 items-center justify-center rounded-full border border-blue-400 bg-blue-500/20 text-blue-300' : completed ? 'flex h-9 w-9 items-center justify-center rounded-full border border-emerald-500/40 bg-emerald-500/20 text-emerald-300' : 'flex h-9 w-9 items-center justify-center rounded-full border border-slate-700 bg-slate-800 text-slate-500'}
              >
                {completed ? <CheckCircle2 className="h-4 w-4" /> : index + 1}
              </div>
              <div>
                <p className={active ? 'text-sm font-semibold text-white' : 'text-sm text-slate-300'}>{phase}</p>
              </div>
            </div>
          );
        })}
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between text-sm text-slate-400">
          <span>Progress</span>
          <span>{progress}%</span>
        </div>
        <div className="h-2 rounded-full bg-slate-800">
          <div className="h-2 rounded-full bg-blue-500 transition-all" style={{ width: `${progress}%` }} />
        </div>
      </div>
      {bytesWritten > 0 ? (
        <p className="text-sm text-slate-300">Bytes written: {formatBytes(bytesWritten)}</p>
      ) : null}
    </Card>
  );
}
