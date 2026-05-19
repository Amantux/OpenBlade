import { Activity, Archive as ArchiveIcon, CassetteTape, LayoutDashboard, ListChecks, Search, Upload } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { cn } from '../../lib/utils';

const links = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard },
  { to: '/library', label: 'Library', icon: ArchiveIcon },
  { to: '/jobs', label: 'Jobs', icon: ListChecks },
  { to: '/archive', label: 'Archive', icon: Upload },
  { to: '/catalog', label: 'Catalog', icon: Search },
  { to: '/health', label: 'Health', icon: Activity },
];

export default function Sidebar() {
  return (
    <aside className="flex min-h-screen w-full max-w-64 flex-col border-r border-slate-800 bg-blade-900 px-4 py-6">
      <div className="flex items-center gap-3 px-3 pb-6 text-white">
        <div className="rounded-xl bg-blue-600/20 p-2 text-blue-300">
          <CassetteTape className="h-6 w-6" />
        </div>
        <div>
          <p className="text-lg font-semibold">OpenBlade</p>
          <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Tape Archive</p>
        </div>
      </div>
      <div className="mb-4 border-t border-slate-800" />
      <nav className="space-y-1">
        {links.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-slate-300 transition hover:bg-slate-800 hover:text-white',
                isActive && 'bg-slate-800 text-white',
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}
