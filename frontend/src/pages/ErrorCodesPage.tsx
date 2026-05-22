import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { getErrorCodes } from '../api/catalogAdmin';

function severityVariant(severity: string): 'red' | 'amber' | 'blue' {
  switch (severity) {
    case 'error':
      return 'red';
    case 'warning':
      return 'amber';
    default:
      return 'blue';
  }
}

export default function ErrorCodesPage() {
  const [severityFilter, setSeverityFilter] = useState<'all' | 'error' | 'warning' | 'info'>('all');
  const [search, setSearch] = useState('');
  const errorCodesQuery = useQuery({
    queryKey: ['error-codes'],
    queryFn: getErrorCodes,
  });

  const rows = useMemo(() => {
    const normalizedSearch = search.trim().toLowerCase();
    return (errorCodesQuery.data ?? []).filter((entry) => {
      const matchesSeverity = severityFilter === 'all' || entry.severity === severityFilter;
      const matchesSearch =
        !normalizedSearch ||
        entry.code.toLowerCase().includes(normalizedSearch) ||
        entry.title.toLowerCase().includes(normalizedSearch) ||
        entry.description.toLowerCase().includes(normalizedSearch) ||
        entry.action.toLowerCase().includes(normalizedSearch);
      return matchesSeverity && matchesSearch;
    });
  }, [errorCodesQuery.data, search, severityFilter]);

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Error codes</h1>
            <p className="mt-2 text-sm text-slate-400">Reference registry for backend error codes and recommended operator action.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search code, title, description"
              className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white"
            />
            <select
              value={severityFilter}
              onChange={(event) => setSeverityFilter(event.target.value as 'all' | 'error' | 'warning' | 'info')}
              className="rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-white"
            >
              <option value="all">All severities</option>
              <option value="error">Error</option>
              <option value="warning">Warning</option>
              <option value="info">Info</option>
            </select>
            <Button variant="secondary" onClick={() => void errorCodesQuery.refetch()}>
              Refresh
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        {errorCodesQuery.isLoading ? <Spinner /> : null}
        {errorCodesQuery.isError ? <ErrorMessage error={errorCodesQuery.error} onRetry={() => errorCodesQuery.refetch()} /> : null}
        {!errorCodesQuery.isLoading && !errorCodesQuery.isError ? (
          rows.length === 0 ? (
            <div className="rounded-md border border-dashed border-quantum-border bg-quantum-panel px-6 py-10 text-center text-sm text-slate-400">
              No error codes match the current filter.
            </div>
          ) : (
            <div className="overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Code</th>
                    <th className="px-4 py-3 font-medium">Severity</th>
                    <th className="px-4 py-3 font-medium">Title</th>
                    <th className="px-4 py-3 font-medium">Description</th>
                    <th className="px-4 py-3 font-medium">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((entry, index) => (
                    <tr key={entry.code} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                      <td className="px-4 py-3 font-mono font-bold text-slate-100">{entry.code}</td>
                      <td className="px-4 py-3"><Badge variant={severityVariant(entry.severity)}>{entry.severity}</Badge></td>
                      <td className="px-4 py-3 text-slate-200">{entry.title}</td>
                      <td className="px-4 py-3 text-slate-300">{entry.description}</td>
                      <td className="px-4 py-3 text-slate-300">{entry.action}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}
