import { useEffect, useState } from 'react';
import { Link, Outlet, useLocation } from 'react-router-dom';
import { useHealth } from '../../hooks/useHealth';
import { useInventory } from '../../hooks/useInventory';
import {
  getActiveLibraryId,
  getActiveLibraryName,
  getActiveLibraryRole,
  subscribeActiveLibrary,
} from '../../lib/activeLibrary';
import { deriveSystemHealth } from '../../lib/utils';
import Sidebar from './Sidebar';
import StatusBar from './StatusBar';
import TopBar from './TopBar';

function isGlobalLibraryScopePath(pathname: string): boolean {
  return pathname.startsWith('/nas/')
    || pathname === '/nas'
    || pathname === '/archive'
    || pathname === '/media/pools'
    || pathname.startsWith('/storage/')
    || pathname === '/file-station'
    || pathname === '/files/browse'
    || pathname === '/gateway';
}

function isLibraryScopedPath(pathname: string): boolean {
  return pathname.startsWith('/libraries/') && pathname !== '/libraries';
}

export default function Layout() {
  const location = useLocation();
  const { health } = useHealth();
  const [activeLibraryId, setActiveLibraryIdState] = useState(() => getActiveLibraryId());
  const [activeLibraryName, setActiveLibraryName] = useState(() => getActiveLibraryName());
  const [activeLibraryRole, setActiveLibraryRole] = useState(() => getActiveLibraryRole());
  const inventoryQuery = useInventory(activeLibraryId);
  const inventory = inventoryQuery.data;
  const systemHealth = deriveSystemHealth(health, inventory?.drives ?? [], inventory?.changer?.state ?? inventory?.changer_state);

  useEffect(
    () => subscribeActiveLibrary((id) => {
      setActiveLibraryIdState(id);
      setActiveLibraryName(getActiveLibraryName());
      setActiveLibraryRole(getActiveLibraryRole());
    }),
    [],
  );

  return (
    <div className="min-h-screen bg-quantum-panel text-slate-100">
      <div className="grid min-h-screen grid-cols-[260px,1fr] grid-rows-[auto,1fr,42px]">
        <div className="row-span-3">
          <Sidebar />
        </div>
        <TopBar
          libraryName={inventory?.library_id ?? 'LIBRARY-01'}
          health={systemHealth}
          backend={health?.backend ?? 'backend'}
          activeLibraryName={activeLibraryId ? activeLibraryName || undefined : undefined}
          activeLibraryRole={activeLibraryRole || undefined}
        />
        <main className="overflow-y-auto bg-quantum-panel p-4">
          {isGlobalLibraryScopePath(location.pathname) ? (
            <div className="mb-4 flex items-center gap-2 text-xs text-slate-400">
              <span className="rounded border border-quantum-border bg-quantum-panel px-2 py-1">Scope: NAS / All Libraries</span>
              <Link to="/libraries" className="text-blue-400 hover:underline">
                Manage libraries
              </Link>
            </div>
          ) : null}
          {isLibraryScopedPath(location.pathname) ? (
            <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-slate-300">
              <span className="rounded border border-quantum-border bg-quantum-panel px-2 py-1">
                Library: {activeLibraryName || `Library ${activeLibraryId || 'Unscoped'}`}
              </span>
              {activeLibraryRole ? (
                <span className="rounded border border-quantum-border bg-quantum-panel px-2 py-1">Role: {activeLibraryRole}</span>
              ) : null}
              <Link to="/libraries" className="text-blue-400 hover:underline">
                Switch library
              </Link>
            </div>
          ) : null}
          <Outlet />
        </main>
        <StatusBar health={health} inventory={inventory} />
      </div>
    </div>
  );
}
