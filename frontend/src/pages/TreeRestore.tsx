import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  listCatalogPrefixes,
  runTreeRestore,
  type TreeRestoreResult,
} from '../api/treeRestore';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatBytes } from '../lib/utils';

function ResultPanel({ result }: { result: TreeRestoreResult }) {
  const failed = result.failures.length > 0;
  return (
    <Card className={failed ? 'border-red-500/30' : undefined}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">
            {result.dryRun ? 'Dry-run preview' : 'Tree restore'}
          </p>
          <h2 className="mt-2 text-xl font-semibold text-white">
            {result.catalogPrefix} → {result.destDir}
          </h2>
          <p className="mt-1 font-mono text-xs text-slate-500">job {result.jobId}</p>
        </div>
        <Badge variant={failed ? 'red' : result.dryRun ? 'blue' : 'green'}>
          {result.dryRun ? 'Planned' : result.status}
        </Badge>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">
            {result.dryRun ? 'Files planned' : 'Files restored'}
          </div>
          <div className="mt-1 text-2xl font-semibold text-white">{result.filesRestored}</div>
        </div>
        <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Bytes</div>
          <div className="mt-1 text-2xl font-semibold text-white">
            {formatBytes(result.bytesRestored)}
          </div>
        </div>
        <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Failed</div>
          <div className="mt-1 text-2xl font-semibold text-white">{result.filesFailed}</div>
        </div>
        <div className="rounded-md border border-quantum-border bg-quantum-panel p-4">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Tapes</div>
          <div className="mt-1 text-2xl font-semibold text-white">{result.tapesUsed.length}</div>
        </div>
      </div>

      {result.tapesUsed.length > 0 ? (
        <div className="mt-4">
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Files per tape</div>
          <ul className="mt-2 space-y-1 text-sm text-slate-300">
            {result.tapesUsed.map((barcode) => (
              <li key={barcode}>
                <span className="font-mono text-red-100">{barcode}</span>{' '}
                {result.perTapeCounts[barcode] ?? 0} file(s)
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.filesSkipped > 0 ? (
        <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
          <div className="font-semibold">
            {result.filesSkipped} catalogued file(s) had nothing archived and were skipped
          </div>
          <ul className="mt-2 space-y-1 font-mono text-xs text-amber-100/80">
            {result.skippedPaths.slice(0, 10).map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
          {result.skippedPaths.length > 10 ? (
            <p className="mt-1 text-xs text-amber-100/70">
              +{result.skippedPaths.length - 10} more
            </p>
          ) : null}
        </div>
      ) : null}

      {failed ? (
        <div className="mt-4 rounded-md border border-red-500/30 bg-red-950/30 p-4 text-sm text-red-100">
          <div className="font-semibold">{result.failures.length} file(s) failed</div>
          <ul className="mt-2 space-y-1 text-xs">
            {result.failures.map((failure) => (
              <li key={failure.catalogPath}>
                <span className="font-mono">{failure.catalogPath}</span> — {failure.error}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </Card>
  );
}

export default function TreeRestore() {
  const [catalogPrefix, setCatalogPrefix] = useState('');
  const [destDir, setDestDir] = useState('');
  const [plan, setPlan] = useState<TreeRestoreResult>();

  const prefixesQuery = useQuery({
    queryKey: ['catalog', 'prefixes'],
    queryFn: () => listCatalogPrefixes(),
  });

  const dryRunMutation = useMutation({
    mutationFn: () =>
      runTreeRestore({ catalogPrefix: catalogPrefix.trim(), destDir: destDir.trim(), dryRun: true }),
    onSuccess: (result) => setPlan(result),
  });

  function changeSelection(next: { prefix?: string; dest?: string }) {
    if (next.prefix !== undefined) {
      setCatalogPrefix(next.prefix);
    }
    if (next.dest !== undefined) {
      setDestDir(next.dest);
    }
    // Every result on screen answers for the OLD selection. Clearing them also
    // re-arms the dry-run gate, so an edited form cannot be run on a stale plan
    // and a finished run cannot be repeated by one more click.
    setPlan(undefined);
    dryRunMutation.reset();
    runMutation.reset();
  }

  const runMutation = useMutation({
    mutationFn: () =>
      runTreeRestore({
        catalogPrefix: catalogPrefix.trim(),
        destDir: destDir.trim(),
        dryRun: false,
      }),
  });

  const ready = Boolean(catalogPrefix.trim() && destDir.trim());
  // The plan is only an answer about the prefix and destination it was run for.
  const planMatchesForm =
    plan !== undefined &&
    plan.destDir === destDir.trim() &&
    // The API normalises the prefix ("photos" -> "/photos"), so compare loosely.
    plan.catalogPrefix.replace(/^\/+|\/+$/g, '') === catalogPrefix.trim().replace(/^\/+|\/+$/g, '');

  return (
    <div className="space-y-4">
      <Card>
        <div className="space-y-2">
          <p className="text-xs uppercase tracking-[0.22em] text-red-300/70">Restore</p>
          <h1 className="text-2xl font-semibold text-white">Tree Restore</h1>
          <p className="max-w-3xl text-sm text-slate-400">
            Restore every archived file under a catalog prefix, spanning as many tapes as it takes.
            The source tree is preserved under the destination: restoring <code>/vg</code> writes{' '}
            <code>/vg/a/x.txt</code> to <code>&lt;destination&gt;/a/x.txt</code>.
          </p>
        </div>
      </Card>

      <Card>
        <p className="text-xs uppercase tracking-[0.18em] text-red-300/70">Selection</p>
        <h2 className="mt-2 text-xl font-semibold text-white">What to restore</h2>

        <div className="mt-4 space-y-4">
          <div>
            <label className="block text-sm text-slate-300" htmlFor="tree-restore-prefix">
              Catalog prefix
            </label>
            <input
              id="tree-restore-prefix"
              value={catalogPrefix}
              onChange={(event) => changeSelection({ prefix: event.target.value })}
              placeholder="/photo-archive"
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
            {prefixesQuery.isLoading ? (
              <p className="mt-2 text-xs text-slate-500">Loading catalog prefixes…</p>
            ) : prefixesQuery.isError ? (
              <p className="mt-2 text-xs text-slate-500">
                Catalog prefixes could not be loaded; type a prefix instead.
              </p>
            ) : (prefixesQuery.data ?? []).length === 0 ? (
              <p className="mt-2 text-xs text-slate-500">
                The catalog lists no archived files yet, so there is no prefix to browse.
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-2">
                {(prefixesQuery.data ?? []).map((prefix) => (
                  <button
                    key={prefix}
                    type="button"
                    onClick={() => changeSelection({ prefix })}
                    className={`rounded-full border px-3 py-1 font-mono text-xs transition ${
                      prefix === catalogPrefix.trim()
                        ? 'border-quantum-red bg-quantum-north text-white'
                        : 'border-quantum-border bg-quantum-panel text-slate-300 hover:bg-quantum-north'
                    }`}
                  >
                    {prefix}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className="block text-sm text-slate-300" htmlFor="tree-restore-dest">
              Destination directory
            </label>
            <input
              id="tree-restore-dest"
              value={destDir}
              onChange={(event) => changeSelection({ dest: event.target.value })}
              placeholder="/restore/photo-archive"
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 font-mono text-sm text-slate-100 outline-none focus:border-quantum-red"
            />
          </div>

          <div className="flex flex-wrap gap-3">
            <Button
              variant="secondary"
              disabled={!ready || dryRunMutation.isPending || runMutation.isPending}
              onClick={() => dryRunMutation.mutate()}
            >
              {dryRunMutation.isPending ? 'Planning…' : 'Dry-run preview'}
            </Button>
            <Button
              disabled={
                !ready ||
                !planMatchesForm ||
                // A finished run is not a licence to run it again: change the form
                // (or re-plan) rather than double-restoring by accident.
                runMutation.data !== undefined ||
                runMutation.isPending ||
                dryRunMutation.isPending
              }
              onClick={() => runMutation.mutate()}
            >
              {runMutation.isPending ? 'Restoring…' : 'Run restore'}
            </Button>
          </div>
          {!planMatchesForm ? (
            <p className="text-xs text-slate-500">
              Run the dry-run preview first — it is the only place the file and tape count is shown
              before media moves.
            </p>
          ) : null}
        </div>
      </Card>

      {dryRunMutation.isPending || runMutation.isPending ? <Spinner /> : null}
      {dryRunMutation.isError ? <ErrorMessage error={dryRunMutation.error} /> : null}
      {runMutation.isError ? <ErrorMessage error={runMutation.error} /> : null}

      {runMutation.data ? (
        <ResultPanel result={runMutation.data} />
      ) : plan ? (
        <ResultPanel result={plan} />
      ) : null}
    </div>
  );
}
