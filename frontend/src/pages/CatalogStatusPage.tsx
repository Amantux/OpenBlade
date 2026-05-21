import { useQuery } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getCatalogStatus } from '../api/catalogAdmin';
import { formatDate, toTitleCase } from '../lib/utils';

export default function CatalogStatusPage() {
  const statusQuery = useQuery({
    queryKey: ['status', 'catalog'],
    queryFn: getCatalogStatus,
  });

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Catalog status</h1>
            <p className="mt-2 text-sm text-slate-400">Database reachability, catalog totals, and the latest rebuild summary.</p>
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
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Total datasets</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.total_datasets}</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Total file records</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.total_file_records}</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Total path mappings</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.total_path_mappings}</div>
            </Card>
            <Card>
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Total cartridges</div>
              <div className="mt-2 text-2xl font-semibold text-white">{statusQuery.data.total_cartridges}</div>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-[0.8fr,1.2fr]">
            <Card>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-lg font-semibold text-white">Database</h2>
                  <p className="mt-1 text-sm text-slate-400">Checked {formatDate(statusQuery.data.checked_at)}</p>
                </div>
                <Badge variant={statusQuery.data.db_reachable ? 'green' : 'red'}>
                  {statusQuery.data.db_reachable ? 'Reachable' : 'Unreachable'}
                </Badge>
              </div>
            </Card>

            <Card>
              <h2 className="text-lg font-semibold text-white">Last rebuild</h2>
              {statusQuery.data.last_rebuild_run_id ? (
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Run ID</div>
                    <div className="mt-1 font-mono text-xs text-slate-100">{statusQuery.data.last_rebuild_run_id}</div>
                  </div>
                  <div className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3">
                    <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Status</div>
                    <div className="mt-1 text-slate-100">{toTitleCase(statusQuery.data.last_rebuild_status ?? 'unknown')}</div>
                  </div>
                </div>
              ) : (
                <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
                  No rebuild runs recorded yet.
                </div>
              )}
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}
