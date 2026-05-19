import { Outlet, useLocation } from 'react-router-dom';
import { deriveSystemHealth } from '../../lib/utils';
import { useHealth } from '../../hooks/useHealth';
import { useInventory } from '../../hooks/useInventory';
import Sidebar from './Sidebar';
import TopBar from './TopBar';

const titles: Record<string, string> = {
  '/': 'Dashboard',
  '/library': 'Library Topology',
  '/jobs': 'Job Orchestration',
  '/archive': 'Archive Workflows',
  '/catalog': 'Catalog & Restore',
  '/health': 'System Health',
};

export default function Layout() {
  const location = useLocation();
  const { health } = useHealth();
  const { data: inventory } = useInventory();
  const systemHealth = deriveSystemHealth(health, inventory?.drives ?? [], inventory?.changer_state);

  return (
    <div className="min-h-screen bg-blade-950 text-slate-100 lg:flex">
      <Sidebar />
      <div className="flex min-h-screen flex-1 flex-col">
        <TopBar
          title={titles[location.pathname] ?? 'OpenBlade'}
          health={systemHealth}
          backend={health?.backend ?? 'unknown'}
        />
        <main className="flex-1 px-6 py-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
