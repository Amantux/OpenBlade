import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getDiagnosticResults, getDiagnosticTests, runDiagnosticTests } from '../api/system';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate, formatDuration } from '../lib/utils';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'passed':
    case 'completed':
      return 'green';
    case 'warn':
    case 'warning':
      return 'amber';
    case 'failed':
      return 'red';
    case 'running':
      return 'blue';
    default:
      return 'gray';
  }
}

export default function SystemDiagnostics() {
  const queryClient = useQueryClient();
  const testsQuery = useQuery({ queryKey: ['system', 'diagnostics', 'tests'], queryFn: getDiagnosticTests, refetchInterval: 60_000 });
  const resultsQuery = useQuery({
    queryKey: ['system', 'diagnostics', 'results'],
    queryFn: getDiagnosticResults,
    refetchInterval: (query) => (query.state.data?.status?.toLowerCase() === 'running' ? 5_000 : 30_000),
    retry: false,
  });

  const runMutation = useMutation({
    mutationFn: (testIds: string[]) => runDiagnosticTests(testIds),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'diagnostics', 'results'] });
    },
  });

  if (testsQuery.isLoading || resultsQuery.isLoading) {
    return <Spinner />;
  }

  if (testsQuery.isError) {
    return <ErrorMessage error={testsQuery.error} onRetry={() => void testsQuery.refetch()} />;
  }

  const tests = testsQuery.data ?? [];
  const results = resultsQuery.isError ? null : resultsQuery.data ?? null;

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Diagnostics</h1>
            <p className="mt-2 text-sm text-slate-400">Run AML diagnostic suites, poll for results, and review pass/fail status from diagnostics test routes.</p>
          </div>
          <Button disabled={runMutation.isPending} onClick={() => runMutation.mutate([])}>{runMutation.isPending ? 'Running…' : 'Run All Tests'}</Button>
        </div>
      </Card>

      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Diagnostic Test List</div>
        <div className="mt-4 space-y-3">
          {tests.map((test) => (
            <div key={test.id} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-4">
              <div>
                <div className="text-sm font-semibold text-slate-100">{test.name}</div>
                <div className="mt-1 text-sm text-slate-400">{test.description}</div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                  <span>{test.category}</span>
                  <span>·</span>
                  <span>~{formatDuration(test.estimatedDuration)}</span>
                </div>
              </div>
              <Button variant="secondary" disabled={runMutation.isPending} onClick={() => runMutation.mutate([test.id])}>
                {runMutation.isPending ? 'Running…' : 'Run Test'}
              </Button>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Latest Results</div>
            <div className="mt-1 text-lg font-semibold text-slate-100">{results ? results.testId : 'No results yet'}</div>
          </div>
          {results ? <Badge variant={statusVariant(results.status)}>{results.status}</Badge> : null}
        </div>

        {resultsQuery.isError ? (
          <div className="mt-4"><ErrorMessage error={resultsQuery.error} onRetry={() => void resultsQuery.refetch()} /></div>
        ) : results ? (
          <div className="mt-4 space-y-4">
            <div className="flex flex-wrap gap-4 text-sm text-slate-300">
              <span>Last run: <span className="text-slate-100">{formatDate(results.endTime)}</span></span>
              <span>Started: <span className="text-slate-100">{formatDate(results.startTime)}</span></span>
              <span>Passed: <span className="text-slate-100">{results.passed}</span></span>
              <span>Failed: <span className="text-slate-100">{results.failed}</span></span>
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-quantum-border text-sm">
                <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
                  <tr>
                    <th className="px-3 py-3">Test</th>
                    <th className="px-3 py-3">Status</th>
                    <th className="px-3 py-3">Message</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-quantum-border/80">
                  {results.details.map((detail) => (
                    <tr key={`${results.id}-${detail.name}`} className="text-slate-200">
                      <td className="px-3 py-3 font-medium text-slate-100">{detail.name}</td>
                      <td className="px-3 py-3"><Badge variant={statusVariant(detail.status)}>{detail.status}</Badge></td>
                      <td className="px-3 py-3">{detail.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="mt-4 rounded-md border border-dashed border-quantum-border px-4 py-8 text-center text-sm text-slate-400">
            Run a diagnostic suite to populate the latest result table.
          </div>
        )}
      </Card>

      {runMutation.isError ? <ErrorMessage error={runMutation.error} /> : null}
    </div>
  );
}
