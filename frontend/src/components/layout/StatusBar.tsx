import type { HealthResponse, InventoryResponse } from '../../types/api';
import { buildSubsystemStatuses, type PanelStatus } from '../../lib/lmc';
import { cn } from '../../lib/utils';

interface StatusBarProps {
  health?: HealthResponse;
  inventory?: InventoryResponse;
}

const stateClasses: Record<PanelStatus, string> = {
  good: 'bg-emerald-400',
  warning: 'bg-amber-400',
  failed: 'bg-red-500',
};

const stateLabels: Record<PanelStatus, string> = {
  good: 'Good',
  warning: 'Warning',
  failed: 'Failed',
};

function SubsystemBadge({ name, state }: { name: string; state: PanelStatus }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-quantum-border bg-quantum-panel px-3 py-1.5">
      <span className={cn('h-2.5 w-2.5 rounded-full', stateClasses[state])} />
      <span className="text-xs font-medium text-slate-200">{name}</span>
      <span className="text-[11px] uppercase tracking-[0.18em] text-slate-500">{stateLabels[state]}</span>
    </div>
  );
}

export default function StatusBar({ health, inventory }: StatusBarProps) {
  const subsystems = buildSubsystemStatuses(health, inventory);

  return (
    <footer className="border-t border-quantum-border bg-quantum-status px-4 py-1.5">
      <div className="flex items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          {subsystems.map((subsystem) => (
            <SubsystemBadge key={subsystem.name} name={subsystem.name} state={subsystem.state} />
          ))}
        </div>
        <div className="text-[11px] uppercase tracking-[0.22em] text-slate-500">Quantum Scalar i3 LMC Emulation</div>
      </div>
    </footer>
  );
}
