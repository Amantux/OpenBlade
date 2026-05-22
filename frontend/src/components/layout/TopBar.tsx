import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import type { SystemHealthLevel } from '../../types/api';
import { useAuth } from '../../lib/auth-context';
import { cn, toTitleCase } from '../../lib/utils';
import StatusPill from '../ui/StatusPill';

interface TopBarProps {
  libraryName: string;
  health: SystemHealthLevel;
  backend: string;
  activeLibraryName?: string;
  activeLibraryRole?: string;
}

const statusCopy: Record<SystemHealthLevel, string> = {
  Healthy: 'Library Ready',
  Degraded: 'Service Attention',
  Critical: 'Operator Required',
};

export default function TopBar({ libraryName, health, backend, activeLibraryName, activeLibraryRole }: TopBarProps) {
  const navigate = useNavigate();
  const auth = useAuth();
  const [now, setNow] = useState(() => new Date());
  const [announcement, setAnnouncement] = useState<string | null>(null);
  const previousActiveLibraryName = useRef(activeLibraryName);
  const username = auth.username ?? 'operator';
  const activeLibraryLabel = activeLibraryName
    ? `${activeLibraryName}${activeLibraryRole ? ` · ${toTitleCase(activeLibraryRole)}` : ''}`
    : 'No Library';
  const activeLibraryChipClassName = cn(
    'mt-2 inline-flex items-center rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.2em] transition',
    activeLibraryName
      ? 'border-blue-500/40 bg-blue-500/10 text-blue-200 hover:bg-blue-500/20'
      : 'border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20',
  );

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (activeLibraryName && previousActiveLibraryName.current !== activeLibraryName) {
      setAnnouncement(`Now managing: ${activeLibraryName}`);
    }
    previousActiveLibraryName.current = activeLibraryName;
  }, [activeLibraryName]);

  useEffect(() => {
    if (!announcement) {
      return;
    }

    const timer = window.setTimeout(() => setAnnouncement(null), 4_000);
    return () => window.clearTimeout(timer);
  }, [announcement]);

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
          {activeLibraryName ? (
            <button
              type="button"
              className={activeLibraryChipClassName}
              onClick={() => navigate('/libraries')}
            >
              {activeLibraryLabel}
            </button>
          ) : (
            <Link to="/libraries" className={activeLibraryChipClassName}>
              {activeLibraryLabel}
            </Link>
          )}
          {announcement ? <div className="mt-2 text-sm font-medium text-emerald-200">{announcement}</div> : null}
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
