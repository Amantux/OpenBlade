import Button from '../ui/Button';

interface OperationAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  variant?: 'primary' | 'secondary' | 'danger';
}

interface OperationsPanelProps {
  title?: string;
  subtitle?: string;
  actions: OperationAction[];
}

export default function OperationsPanel({
  title = 'Available Operations',
  subtitle,
  actions,
}: OperationsPanelProps) {
  return (
    <section className="rounded-md border border-quantum-border bg-quantum-info">
      <div className="border-b border-quantum-border px-4 py-3">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Operations Panel</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-100">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
      </div>
      <div className="flex flex-wrap gap-3 px-4 py-4">
        {actions.map((action) => (
          <Button
            key={action.label}
            variant={action.variant ?? 'secondary'}
            disabled={action.disabled}
            onClick={action.onClick}
          >
            {action.label}
          </Button>
        ))}
      </div>
    </section>
  );
}
