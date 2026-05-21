import { useQuery } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getLibraryStatus } from '../api/catalogAdmin';
import { formatDate, toTitleCase } from '../lib/utils';

function statusVariant(status: string): 'green' | 'amber' | 'red' | 'gray' | 'blue' {
  const normalized = status.toLowerCase();
  if (normalized.includes('ready') || normalized.includes('idle')) {
    return 'green';
  }
  if (normalized.includes('mount') || normalized.includes('run') || normalized.includes('busy')) {
    return 'blue';
  }
  if (normalized.includes('warn')) {
    return 'amber';
  }
  if (normalized.includes('fail') || normalized.includes('error') || normalized.includes('fault')) {
    return 'red';
  }
  return 'gray';
}

export default function LibraryStatusPage() {
  const statusQuery = useQuery({
    queryKey: ['status', 'library'],
    queryFn: getLibraryStatus,
  });

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Library status</h1>
            <p className="mt-2 text-sm text-slate-400">Drive-level status plus slot occupancy summary from the live library inventory.</p>
          </div>
          <Button variant="secondary" onClick={() => void statusQuery.refetch()}>
            Refresh
          </Button>
        </div>
      </Card>

      {statusQuery.isLoading ? <Spinner /> : null}
      {statusQuery.isError ? <ErrorMessage error={statusQuery.error} onRetry={() => statusQuery.refetch()} /> : null}
      {statusQuery.data ? (
        <>
          <div className="grid gap-4 md:grid-cols-4">
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Library link</div>
              <div className="mt-2"><Badge variant={statusQuery.data.library_connected ? 'green' : 'red'}>{statusQuery.data.library_connected ? 'Connected' : 'Disconnected'}</Badge></div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Slots total</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.slots_total}</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Slots occupied</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.slots_occupied}</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Cartridges loaded</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.cartridges_loaded}</div>
            </Card>
          </div>

          <Card>
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-white">Drives</h2>
                <p className="mt-1 text-sm text-slate-400">Updated {formatDate(statusQuery.data.last_updated_at)}</p>
              </div>
            </div>

            {statusQuery.data.drives.length === 0 ? (
              <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
                No drives reported.
              </div>
            ) : (
              <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                {statusQuery.data.drives.map((drive, index) => {
                  const driveStatus = drive.drive_state ?? drive.status ?? 'unknown';
                  const loadedBarcode = drive.loaded_barcode ?? drive.barcode ?? '—';
                  const lastOperation = drive.last_operation ?? drive.mount_state ?? 'No recent operation';
                  return (
                    <div key={`${drive.drive_id ?? index}-${loadedBarcode}`} className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div>
                          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Drive index</div>
                          <div className="mt-1 text-lg font-semibold text-white">{drive.drive_index ?? drive.drive_id ?? index + 1}</div>
                        </div>
                        <Badge variant={statusVariant(driveStatus)}>{toTitleCase(driveStatus)}</Badge>
                      </div>
                      <div className="mt-4 space-y-3 text-sm text-slate-300">
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Loaded barcode</div>
                          <div className="mt-1 text-slate-100">{loadedBarcode}</div>
                        </div>
                        <div>
                          <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Last operation</div>
                          <div className="mt-1 text-slate-100">{toTitleCase(lastOperation)}</div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </Card>
        </>
      ) : null}
    </div>
  );
}
