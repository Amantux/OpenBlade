import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { useHealth } from '../../hooks/useHealth';
import { useInventory } from '../../hooks/useInventory';
import { getActiveLibraryId, getActiveLibraryName, subscribeActiveLibrary } from '../../lib/activeLibrary';
import { deriveSystemHealth } from '../../lib/utils';
import Sidebar from './Sidebar';
import StatusBar from './StatusBar';
import TopBar from './TopBar';

export default function Layout() {
  const { health } = useHealth();
  const inventoryQuery = useInventory();
  const inventory = inventoryQuery.data;
  const [activeLibraryId, setActiveLibraryId] = useState(() => getActiveLibraryId());
  const [activeLibraryName, setActiveLibraryName] = useState(() => getActiveLibraryName());
  const systemHealth = deriveSystemHealth(health, inventory?.drives ?? [], inventory?.changer?.state ?? inventory?.changer_state);

  useEffect(
    () => subscribeActiveLibrary((id) => {
      setActiveLibraryId(id);
      setActiveLibraryName(getActiveLibraryName());
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
        />
        <main className="overflow-y-auto bg-quantum-panel p-4">
          <Outlet />
        </main>
        <StatusBar health={health} inventory={inventory} />
      </div>
    </div>
  );
}
