import type { ReactNode } from 'react';

interface InformationPanelItem {
  label: string;
  value: ReactNode;
}

interface InformationPanelProps {
  title: string;
  subtitle?: string;
  items: InformationPanelItem[];
}

export default function InformationPanel({ title, subtitle, items }: InformationPanelProps) {
  return (
    <section className="rounded-md border border-quantum-border bg-quantum-info">
      <div className="border-b border-quantum-border px-4 py-3">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Information Panel</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-100">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
      </div>
      <div className="grid gap-3 px-4 py-4 md:grid-cols-2 xl:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className="rounded-md border border-quantum-border bg-quantum-panel px-3 py-2">
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{item.label}</div>
            <div className="mt-2 break-words text-sm font-medium text-slate-100">{item.value}</div>
          </div>
        ))}
      </div>
    </section>
  );
}
