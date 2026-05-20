import type { SystemHealthLevel } from '../../types/api';
import { cn } from '../../lib/utils';

interface StatusPillProps {
  status: SystemHealthLevel;
}

const pillClasses: Record<SystemHealthLevel, string> = {
  Healthy: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  Degraded: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  Critical: 'border-red-500/30 bg-red-500/15 text-red-300',
};

const dotClasses: Record<SystemHealthLevel, string> = {
  Healthy: 'bg-emerald-400',
  Degraded: 'bg-amber-400',
  Critical: 'bg-red-400',
};

export default function StatusPill({ status }: StatusPillProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm font-semibold',
        pillClasses[status],
      )}
    >
      <span className={cn('h-2.5 w-2.5 rounded-full', dotClasses[status])} />
      {status}
    </span>
  );
}
