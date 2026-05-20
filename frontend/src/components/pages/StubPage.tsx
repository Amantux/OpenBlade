import type { ReactNode } from 'react';
import Badge from '../ui/Badge';
import Card from '../ui/Card';

interface StubPageProps {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}

export default function StubPage({ eyebrow, title, description, children }: StubPageProps) {
  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">{eyebrow}</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">{title}</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">{description}</p>
          </div>
          <Badge variant="blue">Coming in next build</Badge>
        </div>
      </Card>
      {children ? (
        children
      ) : (
        <Card className="bg-quantum-info">
          <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Planned capabilities</div>
          <div className="mt-3 grid gap-3 md:grid-cols-3">
            {['Operator workflows', 'Credential-aware actions', 'Quantum-style drilldowns'].map((item) => (
              <div key={item} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4 text-sm text-slate-300">
                {item}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
