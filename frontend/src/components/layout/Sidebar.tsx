import {
  Activity,
  ChevronDown,
  ChevronRight,
  Gauge,
  Layers3,
  Network,
  Search,
  ServerCog,
} from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  getActiveLibraryName,
  getActiveLibraryRole,
  subscribeActiveLibrary,
} from '../../lib/activeLibrary';
import { cn, toTitleCase } from '../../lib/utils';
import { apiRequest } from '../../api/client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface NavItem {
  label: string;
  to: string;
  end?: boolean;
  group?: string;
}

interface NavSection {
  id: string;
  label: string;
  icon: typeof Layers3;
  items: NavItem[];
  badgeQuery?: () => Promise<number>; // optional live badge
}

// ---------------------------------------------------------------------------
// Section definitions
// ---------------------------------------------------------------------------

const sections: NavSection[] = [
  {
    id: 'dashboard',
    label: 'Dashboard',
    icon: Gauge,
    items: [
      { label: 'Dashboard', to: '/', end: true },
      { label: 'Fleet Overview', to: '/libraries' },
      { label: 'System Health', to: '/system/health' },
    ],
  },
  {
    id: 'nas',
    label: 'NAS',
    icon: ServerCog,
    items: [
      { label: 'File Station', to: '/nas/file-station', group: 'Files & Access' },
      { label: 'File Browser', to: '/nas/browser', group: 'Files & Access' },
      { label: 'Shares', to: '/nas/shares', group: 'Files & Access' },
      { label: 'Virtual Pools', to: '/nas/pools', group: 'Files & Access' },
      { label: 'Restore Queue', to: '/nas/restore-queue', group: 'Files & Access' },
      { label: 'Archive Planning', to: '/nas/archive-planning', group: 'Archive' },
      { label: 'Storage Policies', to: '/nas/policies', group: 'Ingest' },
      { label: 'Cache Drives', to: '/nas/cache-drives', group: 'Ingest' },
      { label: 'Source Streaming', to: '/nas/source-streaming', group: 'Ingest' },
      { label: 'Protocol Gateway', to: '/nas/gateway', group: 'Files & Access' },
      { label: 'Dataset Details', to: '/storage/dataset-details', group: 'Archive' },
    ],
  },
  {
    id: 'libraries',
    label: 'Libraries',
    icon: Layers3,
    items: [
      { label: 'Fleet Overview', to: '/libraries', end: true, group: 'Fleet' },
      { label: 'Overview', to: '/library', group: 'Items' },
      { label: 'Physical Map', to: '/library', group: 'Items' },
      { label: 'Inventory', to: '/library/inventory', group: 'Items' },
      { label: 'Cartridges', to: '/media', group: 'Items' },
      { label: 'Drives', to: '/drives', group: 'Items' },
      { label: 'Partitions', to: '/partitions', group: 'Items' },
      { label: 'IE Station', to: '/library/ie', group: 'Items' },
      { label: 'LTFS Browse', to: '/media/ltfs', group: 'Items' },
      { label: 'Jobs', to: '/jobs', group: 'Items' },
      { label: 'Move Operations', to: '/operations/move', group: 'Items' },
      { label: 'Inventory Scan', to: '/operations/inventory', group: 'Items' },
      { label: 'Import / Export', to: '/operations/ie', group: 'Items' },
      { label: 'Status', to: '/system/library', group: 'Admin' },
      { label: 'Diagnostics', to: '/system/diagnostics', group: 'Admin' },
      { label: 'Safety', to: '/admin/safety', group: 'Admin' },
    ],
  },
  {
    id: 'system',
    label: 'System',
    icon: Network,
    items: [
      { label: 'System Info', to: '/system', end: true, group: 'System' },
      { label: 'Health', to: '/system/health', group: 'System' },
      { label: 'Network', to: '/system/network', group: 'System' },
      { label: 'Configuration', to: '/system/config', group: 'System' },
      { label: 'Firmware', to: '/system/firmware', group: 'System' },
      { label: 'Security', to: '/admin/security', group: 'System' },
      { label: 'Test Runner', to: '/system/test-runner', group: 'Testing' },
      { label: 'Catalog Status', to: '/system/catalog', group: 'System' },
      { label: 'Error Codes', to: '/system/error-codes', group: 'System' },
      { label: 'Catalog Records', to: '/catalog', group: 'System' },
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

// ---------------------------------------------------------------------------
// localStorage helpers for sidebar expanded state (UX-4)
// ---------------------------------------------------------------------------

const SIDEBAR_STORAGE_KEY = 'openblade_sidebar_expanded';

function loadExpandedState(): Record<string, boolean> {
  const defaults = Object.fromEntries(sections.map(s => [s.id, true]));
  try {
    const raw = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (raw) {
      return { ...defaults, ...(JSON.parse(raw) as Record<string, boolean>) };
    }
  } catch {
    // ignore
  }
  return defaults;
}

function saveExpandedState(state: Record<string, boolean>) {
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore
  }
}

// ---------------------------------------------------------------------------
// Command Palette (UX-5)
// ---------------------------------------------------------------------------

interface CommandItem {
  id: string;
  label: string;
  to?: string;
  action?: () => void;
  category: string;
}

function CommandPalette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const allItems: CommandItem[] = sections.flatMap(s =>
    s.items.map(item => ({
      id: item.to,
      label: item.label,
      to: item.to,
      category: s.label,
    }))
  );

  const filtered = query.trim()
    ? allItems.filter(item =>
        item.label.toLowerCase().includes(query.toLowerCase()) ||
        item.category.toLowerCase().includes(query.toLowerCase())
      )
    : allItems.slice(0, 12);

  function select(item: CommandItem) {
    if (item.to) navigate(item.to);
    if (item.action) item.action();
    onClose();
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-quantum-border bg-quantum-panel shadow-2xl shadow-black/60"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-quantum-border px-4 py-3">
          <Search className="h-4 w-4 text-slate-400 flex-shrink-0" />
          <input
            ref={inputRef}
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Escape') onClose();
              if (e.key === 'Enter' && filtered.length > 0) select(filtered[0]);
            }}
            placeholder="Search pages, settings…"
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-slate-500 outline-none"
          />
          <kbd className="rounded border border-quantum-border px-1.5 py-0.5 text-xs text-slate-500">Esc</kbd>
        </div>
        <div className="max-h-[360px] overflow-y-auto py-2">
          {filtered.length === 0 && (
            <div className="px-4 py-3 text-sm text-slate-500">No results for "{query}"</div>
          )}
          {filtered.map(item => (
            <button
              key={item.id}
              type="button"
              onClick={() => select(item)}
              className="flex w-full items-center justify-between px-4 py-2 text-sm text-slate-300 hover:bg-quantum-north hover:text-white"
            >
              <span>{item.label}</span>
              <span className="text-xs text-slate-500">{item.category}</span>
            </button>
          ))}
        </div>
        <div className="border-t border-quantum-border px-4 py-2 text-xs text-slate-500">
          Press <kbd className="rounded border border-quantum-border px-1">↵</kbd> to select
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Grouped nav items renderer
// ---------------------------------------------------------------------------

function GroupedNavItems({ items }: { items: NavItem[] }) {
  const hasGroups = items.some(i => i.group);
  if (!hasGroups) {
    return (
      <>
        {items.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end ?? false}
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
      </>
    );
  }

  // Build group map preserving order
  const groups: string[] = [];
  const groupMap: Record<string, NavItem[]> = {};
  const ungrouped: NavItem[] = [];
  for (const item of items) {
    if (item.group) {
      if (!groupMap[item.group]) {
        groups.push(item.group);
        groupMap[item.group] = [];
      }
      groupMap[item.group].push(item);
    } else {
      ungrouped.push(item);
    }
  }

  return (
    <>
      {ungrouped.map(item => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end ?? false}
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
      {groups.map(group => (
        <div key={group} className="mt-2">
          <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-slate-600">
            {group}
          </div>
          {groupMap[group].map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end ?? false}
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
      ))}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main Sidebar
// ---------------------------------------------------------------------------

export default function Sidebar() {
  const [activeLibraryName, setActiveLibraryName] = useState(() => getActiveLibraryName());
  const [activeLibraryRole, setActiveLibraryRole] = useState(() => getActiveLibraryRole());
  const [expanded, setExpanded] = useState<Record<string, boolean>>(loadExpandedState);
  const [showPalette, setShowPalette] = useState(false);

  // UX-3: Live active job count (polling every 10 s)
  const { data: activeJobCount } = useQuery({
    queryKey: ['sidebar-active-jobs'],
    queryFn: async () => {
      try {
        const resp = await apiRequest<{ jobList?: { job?: { status?: string }[] } }>('/jobs');
        const jobs = resp.jobList?.job ?? [];
        return jobs.filter(j => {
          const s = (j.status ?? '').toLowerCase();
          return s === 'active' || s === 'running';
        }).length;
      } catch {
        return 0;
      }
    },
    refetchInterval: 10_000,
    staleTime: 9_000,
  });

  useEffect(
    () => subscribeActiveLibrary(() => {
      setActiveLibraryName(getActiveLibraryName());
      setActiveLibraryRole(getActiveLibraryRole());
    }),
    [],
  );

  // UX-5: Cmd+K / Ctrl+K keyboard shortcut
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setShowPalette(prev => !prev);
      }
      if (e.key === 'Escape') setShowPalette(false);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  function toggleSection(id: string) {
    setExpanded(current => {
      const next = { ...current, [id]: !current[id] };
      saveExpandedState(next); // UX-4: persist
      return next;
    });
  }

  const activeLibraryLabel = activeLibraryName
    ? `⬡ ${activeLibraryName}${activeLibraryRole ? ` · ${toTitleCase(activeLibraryRole)}` : ''}`
    : 'No Library';

  return (
    <>
      {showPalette && <CommandPalette onClose={() => setShowPalette(false)} />}

      <aside className="flex min-h-screen w-[280px] flex-col border-r border-quantum-border bg-quantum-sidebar">
        <div className="border-b border-quantum-border px-4 py-4">
          <div className="text-xs uppercase tracking-[0.32em] text-slate-500">Quantum Scalar i3</div>
          <div className="mt-2 text-lg font-semibold text-slate-100">
            {activeLibraryName ? `OpenBlade · ${activeLibraryName}` : 'OpenBlade Control Plane'}
          </div>
          <div className="mt-1 text-xs text-slate-400">Modern SaaS telemetry + operator workflows</div>
          {/* UX-5: Cmd+K hint */}
          <button
            type="button"
            onClick={() => setShowPalette(true)}
            className="mt-2 flex w-full items-center gap-2 rounded border border-quantum-border bg-quantum-north px-2 py-1.5 text-xs text-slate-500 hover:border-quantum-red/40 hover:text-slate-300 transition"
          >
            <Search className="h-3 w-3" />
            <span className="flex-1 text-left">Search pages…</span>
            <kbd className="rounded border border-quantum-border px-1">⌘K</kbd>
          </button>
        </div>

        <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4">
          {sections.map((section) => {
            const Icon = section.icon;
            const isExpanded = expanded[section.id];
            const showJobBadge = section.id === 'operations' && (activeJobCount ?? 0) > 0;

            return (
              <div key={section.id} className="rounded-md border border-transparent bg-transparent">
                <button
                  type="button"
                  className="flex w-full items-center justify-between rounded-md px-3 py-2 text-left text-sm font-semibold uppercase tracking-[0.18em] text-slate-300 hover:bg-quantum-north"
                  onClick={() => toggleSection(section.id)}
                >
                  <span className="flex items-center gap-2">
                    <Icon className="h-4 w-4 text-quantum-red" />
                    {section.label}
                    {/* UX-3: Live job badge */}
                    {showJobBadge && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-blue-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-blue-300">
                        <span className="h-1.5 w-1.5 rounded-full bg-blue-400 animate-pulse" />
                        {activeJobCount}
                      </span>
                    )}
                  </span>
                  {isExpanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                </button>

                {isExpanded ? (
                  <div className="mt-1 space-y-1 pl-2">
                    {section.id === 'libraries' ? (
                      <NavLink
                        to="/libraries"
                        className={({ isActive }) =>
                          cn(
                            'mx-3 mb-2 block truncate rounded border px-2 py-1 text-xs transition',
                            activeLibraryName
                              ? 'border-quantum-border bg-quantum-panel text-slate-300 hover:border-quantum-red/40 hover:text-white'
                              : 'border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500/20',
                            isActive && activeLibraryName && 'border-quantum-red/60 text-white',
                          )
                        }
                      >
                        {activeLibraryLabel}
                      </NavLink>
                    ) : null}
                    <GroupedNavItems items={section.items} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
