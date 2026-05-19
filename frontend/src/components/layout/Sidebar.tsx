import { ChevronDown, ChevronRight, Cpu, HardDrive, Layers3, Shield, SquareStack, Workflow } from 'lucide-react';
import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { cn } from '../../lib/utils';

interface NavItem {
  label: string;
  to: string;
}

interface NavSection {
  id: string;
  label: string;
  icon: typeof Layers3;
  items: NavItem[];
}

const sections: NavSection[] = [
  {
    id: 'library',
    label: 'Library',
    icon: Layers3,
    items: [
      { label: 'Overview', to: '/library' },
      { label: 'Inventory', to: '/library/inventory' },
      { label: 'IE Area', to: '/library/inventory#ie-area' },
      { label: 'Cleaning Slots', to: '/library/inventory#cleaning-slots' },
    ],
  },
  {
    id: 'drives',
    label: 'Drives',
    icon: HardDrive,
    items: [
      { label: 'Drive Overview', to: '/drives' },
      { label: 'Drive States', to: '/drives#drive-states' },
    ],
  },
  {
    id: 'devices',
    label: 'Devices',
    icon: Cpu,
    items: [
      { label: 'Robots', to: '/health#robots' },
      { label: 'iBlades', to: '/health#iblades' },
    ],
  },
  {
    id: 'system',
    label: 'System',
    icon: Shield,
    items: [
      { label: 'Configuration', to: '/health#configuration' },
      { label: 'Health', to: '/health' },
    ],
  },
  {
    id: 'reports',
    label: 'Reports',
    icon: SquareStack,
    items: [
      { label: 'RAS Tickets', to: '/health#ras-tickets' },
      { label: 'Activity Log', to: '/jobs#activity-log' },
    ],
  },
  {
    id: 'jobs',
    label: 'Jobs',
    icon: Workflow,
    items: [
      { label: 'Active Jobs', to: '/jobs' },
      { label: 'Archive', to: '/archive' },
    ],
  },
];

export default function Sidebar() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    library: true,
    drives: true,
    devices: false,
    system: true,
    reports: true,
    jobs: true,
  });

  return (
    <aside className="flex min-h-screen w-[260px] flex-col border-r border-quantum-border bg-quantum-sidebar">
      <div className="border-b border-quantum-border px-4 py-4">
        <div className="text-xs uppercase tracking-[0.32em] text-slate-500">Quantum Scalar i3</div>
        <div className="mt-2 text-lg font-semibold text-slate-100">Library Management Console</div>
        <div className="mt-1 text-xs text-slate-400">Desktop operator interface emulation</div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
        {sections.map((section) => {
          const Icon = section.icon;
          const isExpanded = expanded[section.id];

          return (
            <div key={section.id} className="rounded-md border border-transparent bg-transparent">
              <button
                type="button"
                className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-semibold uppercase tracking-[0.18em] text-slate-300 hover:bg-quantum-north"
                onClick={() =>
                  setExpanded((current) => ({
                    ...current,
                    [section.id]: !current[section.id],
                  }))
                }
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4 text-quantum-red" />
                  {section.label}
                </span>
                {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
              </button>

              {isExpanded ? (
                <div className="mt-1 space-y-1 pl-2">
                  {section.items.map((item) => (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={({ isActive }) =>
                        cn(
                          'flex items-center rounded-md border-l-2 border-transparent px-3 py-2 text-sm text-slate-300 transition hover:bg-quantum-north hover:text-white',
                          isActive && 'border-quantum-red bg-quantum-north text-white',
                        )
                      }
                    >
                      {item.label}
                    </NavLink>
                  ))}
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
