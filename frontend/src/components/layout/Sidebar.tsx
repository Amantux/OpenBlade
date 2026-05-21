import {
  Activity,
  ChevronDown,
  ChevronRight,
  Database,
  Gauge,
  HardDrive,
  Layers3,
  Network,
  ServerCog,
  Shield,
  Workflow,
} from 'lucide-react';
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
    id: 'overview',
    label: 'Overview',
    icon: Gauge,
    items: [
      { label: 'Dashboard', to: '/' },
      { label: 'Multi-Library Grid', to: '/libraries' },
      { label: 'Health & Alerts', to: '/health' },
    ],
  },
  {
    id: 'library',
    label: 'Library',
    icon: Layers3,
    items: [
      { label: 'Physical Map', to: '/library' },
      { label: 'Inventory', to: '/library/inventory' },
      { label: 'Partitions', to: '/partitions' },
      { label: 'IE Station', to: '/library/ie' },
    ],
  },
  {
    id: 'media',
    label: 'Media',
    icon: Database,
    items: [
      { label: 'Cartridges', to: '/media' },
      { label: 'Archive', to: '/archive' },
      { label: 'Media Pools', to: '/media/pools' },
      { label: 'LTFS Browse', to: '/media/ltfs' },
    ],
  },
  {
    id: 'catalog',
    label: 'Catalog',
    icon: Database,
    items: [
      { label: 'Catalog Records', to: '/catalog' },
      { label: 'Rebuild', to: '/catalog/rebuild' },
      { label: 'Manifest Versions', to: '/catalog/manifests' },
    ],
  },
  {
    id: 'drives',
    label: 'Drives',
    icon: HardDrive,
    items: [
      { label: 'Drive Overview', to: '/drives' },
      { label: 'Drive Operations', to: '/drives/ops' },
    ],
  },
  {
    id: 'operations',
    label: 'Operations',
    icon: Workflow,
    items: [
      { label: 'Job Queue', to: '/jobs' },
      { label: 'Move Operations', to: '/operations/move' },
      { label: 'Inventory Scan', to: '/operations/inventory' },
      { label: 'Import / Export', to: '/operations/ie' },
    ],
  },
  {
    id: 'storage',
    label: 'Storage',
    icon: ServerCog,
    items: [
      { label: 'Storage Policies', to: '/storage/policies' },
      { label: 'Cache Drives', to: '/storage/cache-drives' },
      { label: 'Source Streaming', to: '/storage/source-streaming' },
      { label: 'Archive Planning', to: '/storage/archive-planning' },
      { label: 'Virtual Pools', to: '/storage/virtual-pools' },
      { label: 'Restore Queue', to: '/storage/restore-queue' },
      { label: 'Dataset Details', to: '/storage/dataset-details' },
      { label: 'File Station', to: '/file-station' },
      { label: 'File Browser', to: '/files/browse' },
      { label: 'Protocol Gateway', to: '/gateway' },
    ],
  },
  {
    id: 'admin',
    label: 'Admin',
    icon: Shield,
    items: [
      { label: 'Security', to: '/admin/security' },
      { label: 'Safety', to: '/admin/safety' },
    ],
  },
  {
    id: 'system',
    label: 'System',
    icon: Network,
    items: [
      { label: 'System Info', to: '/system' },
      { label: 'Health', to: '/system/health' },
      { label: 'Error Codes', to: '/system/error-codes' },
      { label: 'Library Status', to: '/system/library' },
      { label: 'Catalog Status', to: '/system/catalog' },
      { label: 'Network', to: '/system/network' },
      { label: 'Configuration', to: '/system/config' },
      { label: 'Firmware', to: '/system/firmware' },
      { label: 'Diagnostics', to: '/system/diagnostics' },
    ],
  },
  {
    id: 'reports',
    label: 'Reports',
    icon: Activity,
    items: [
      { label: 'RAS Tickets', to: '/reports/ras' },
      { label: 'Events Log', to: '/reports/events' },
      { label: 'Activity', to: '/reports/activity' },
    ],
  },
];

export default function Sidebar() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    overview: true,
    library: true,
    media: true,
    catalog: true,
    drives: true,
    operations: true,
    storage: true,
    admin: true,
    system: true,
    reports: true,
    gateway: true,
  });

  return (
    <aside className="flex min-h-screen w-[280px] flex-col border-r border-quantum-border bg-quantum-sidebar">
      <div className="border-b border-quantum-border px-4 py-4">
        <div className="text-xs uppercase tracking-[0.32em] text-slate-500">Quantum Scalar i3</div>
        <div className="mt-2 text-lg font-semibold text-slate-100">OpenBlade Control Plane</div>
        <div className="mt-1 text-xs text-slate-400">Modern SaaS telemetry + operator workflows</div>
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
                      end={item.to === '/'}
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
