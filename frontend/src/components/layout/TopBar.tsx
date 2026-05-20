import { useEffect, useState } from 'react';
import type { SystemHealth } from '../../types/api';
import { getStoredUsername } from '../../lib/auth';
import StatusPill from '../ui/StatusPill';

interface TopBarProps {
  libraryName: string;
  health: SystemHealth;
  backend: string;
}

const statusCopy: Record<SystemHealth, string> = {
  Healthy: 'Library Ready',
  Degraded: 'Service Attention',
  Critical: 'Operator Required',
};

export default function TopBar({ libraryName, health, backend }: TopBarProps) {
  const [now, setNow] = useState(() => new Date());
  const [username] = useState(() => getStoredUsername() ?? 'operator');

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <header className="border-b border-quantum-border bg-quantum-navy px-5 py-3">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-sm bg-quantum-red text-xl font-bold text-white">
            Q
          </div>
          <div>
            <div className="text-lg font-semibold tracking-wide text-quantum-red">Scalar i3</div>
            <div className="text-xs uppercase tracking-[0.28em] text-slate-400">OpenBlade</div>
          </div>
        </div>

        <div className="min-w-0 flex-1 text-center">
          <div className="text-sm font-semibold tracking-[0.22em] text-slate-200">{libraryName}</div>
          <div className="mt-1 text-xs uppercase tracking-[0.2em] text-slate-400">{statusCopy[health]}</div>
        </div>

        <div className="flex items-center gap-3 text-right text-xs text-slate-300">
          <StatusPill status={health} />
          <div className="hidden border-l border-quantum-border pl-3 md:block">
            <div className="uppercase tracking-[0.18em] text-slate-500">Interface</div>
            <div className="mt-1 font-medium text-slate-200">AML</div>
          </div>
          <div className="hidden border-l border-quantum-border pl-3 md:block">
            <div className="uppercase tracking-[0.18em] text-slate-500">User</div>
            <div className="mt-1 font-medium text-slate-200">{username}</div>
          </div>
          <div className="border-l border-quantum-border pl-3">
            <div className="uppercase tracking-[0.18em] text-slate-500">Time</div>
            <div className="mt-1 font-medium text-slate-200">{now.toLocaleString()}</div>
            <div className="mt-1 text-[11px] uppercase tracking-[0.16em] text-slate-500">{backend}</div>
          </div>
        </div>
      </div>
    </header>
  );
}
