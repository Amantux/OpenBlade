import type { SystemHealth } from '../../types/api';
import StatusPill from '../ui/StatusPill';

interface TopBarProps {
  title: string;
  health: SystemHealth;
  backend: string;
}

export default function TopBar({ title, health, backend }: TopBarProps) {
  return (
    <header className="sticky top-0 z-10 border-b border-slate-800 bg-blade-950/90 px-6 py-4 backdrop-blur">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.3em] text-slate-500">OpenBlade Console</p>
          <h1 className="mt-1 text-2xl font-semibold text-white">{title}</h1>
        </div>
        <div className="flex items-center gap-3">
          <StatusPill status={health} />
          <span className="rounded-full border border-slate-700 bg-slate-800 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-slate-300">
            Backend {backend}
          </span>
        </div>
      </div>
    </header>
  );
}
