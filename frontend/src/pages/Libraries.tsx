import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { listLibraries, type LibrarySummary } from '../api/libraries';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getActiveLibraryId, setActiveLibraryId, subscribeActiveLibrary } from '../lib/activeLibrary';

function statusAppearance(status: string | undefined): { label: string; className: string } {
  switch (status) {
    case 'online':
      return { label: 'Online', className: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300' };
    case 'offline':
      return { label: 'Offline', className: 'border-red-500/30 bg-red-500/15 text-red-300' };
    case 'error':
      return { label: 'Error', className: 'border-amber-500/30 bg-amber-500/15 text-amber-300' };
    default:
      return { label: 'Unknown', className: 'border-slate-600 bg-slate-800/90 text-slate-300' };
  }
}

export default function Libraries() {
  const navigate = useNavigate();
  const [activeLibraryId, setActiveLibraryIdState] = useState(() => getActiveLibraryId());
  const librariesQuery = useQuery({ queryKey: ['libraries'], queryFn: listLibraries, refetchInterval: 30_000 });

  useEffect(() => subscribeActiveLibrary(setActiveLibraryIdState), []);

  useEffect(() => {
    if (!activeLibraryId && (librariesQuery.data ?? []).length > 0) {
      const firstLibrary = librariesQuery.data?.[0];
      if (firstLibrary) {
        setActiveLibraryId(String(firstLibrary.id), firstLibrary.name);
      }
    }
  }, [activeLibraryId, librariesQuery.data]);

  if (librariesQuery.isLoading && !librariesQuery.data) {
    return <Spinner />;
  }

  if (librariesQuery.isError) {
    return <ErrorMessage error={librariesQuery.error} onRetry={() => void librariesQuery.refetch()} />;
  }

  const libraries = librariesQuery.data ?? [];

  const handleSelect = (library: LibrarySummary) => {
    setActiveLibraryId(String(library.id), library.name);
    navigate('/library');
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Overview</div>
          <h1 className="mt-1 text-2xl font-semibold text-slate-100">Library Grid</h1>
          <p className="mt-1 text-sm text-slate-400">Select between provisioned OpenBlade libraries and keep the active context synced across the UI.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge variant="blue">{libraries.length} libraries</Badge>
          <Button type="button" variant="secondary" onClick={() => void librariesQuery.refetch()}>
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {libraries.map((library) => {
          const appearance = statusAppearance(library.status);
          const isActive = activeLibraryId === String(library.id);
          return (
            <Card key={library.id} className="bg-quantum-info p-5">
              <div className="flex h-full flex-col gap-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-semibold text-slate-100">{library.name}</h2>
                      {isActive ? <Badge variant="green">Active</Badge> : null}
                    </div>
                    <p className="mt-1 text-sm text-slate-400">{library.model} · {library.serial_number ?? 'No serial'}</p>
                  </div>
                  <span className={`inline-flex items-center rounded-full border px-3 py-1 text-sm font-semibold ${appearance.className}`}>
                    {appearance.label}
                  </span>
                </div>

                <div className="grid gap-2 rounded-md border border-quantum-border bg-quantum-panel px-3 py-3 text-sm text-slate-300">
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Endpoint</span>
                    <span className="truncate text-right">{library.emulator_url}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Drives</span>
                    <span>{library.drive_count}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Tapes</span>
                    <span>{library.tape_count}</span>
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <span className="text-slate-500">Enabled</span>
                    <span>{library.enabled ? 'Yes' : 'No'}</span>
                  </div>
                </div>

                <div className="mt-auto flex flex-wrap gap-2">
                  <Button type="button" variant={isActive ? 'secondary' : 'primary'} onClick={() => handleSelect(library)}>
                    {isActive ? 'Selected' : 'Select Library'}
                  </Button>
                  <Button type="button" variant="secondary" onClick={() => navigate('/dashboard')}>
                    Dashboard
                  </Button>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
