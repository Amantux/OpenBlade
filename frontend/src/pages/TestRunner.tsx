import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, CheckCircle, ChevronDown, ChevronRight, Download, FlaskConical, Loader2, Play, XCircle } from 'lucide-react';
import Badge from '../components/ui/Badge';
import { getRunStatus, listRuns, openRunStream, startTestRun } from '../api/testRunner';
import type { RunStatus, TestRunStatus, TestRunSummary, TimingProfile, TestTarget } from '../api/testRunner';
import { cn } from '../lib/utils';

// ---------------------------------------------------------------------------
// Module definitions
// ---------------------------------------------------------------------------

const TEST_MODULES = [
  { id: 'test_01_auth', label: 'Auth', description: 'Login, session, bad credentials' },
  { id: 'test_02_inventory', label: 'Inventory', description: 'Slots, drives, physical map' },
  { id: 'test_03_changer', label: 'Changer', description: 'Load/unload/move + timing' },
  { id: 'test_04_drives', label: 'Drives', description: 'Drive health, status transitions' },
  { id: 'test_05_media', label: 'Media', description: 'Cartridge lifecycle, pools' },
  { id: 'test_06_operations', label: 'Operations', description: 'Move wizard, IE door, queue' },
  { id: 'test_07_ltfs', label: 'LTFS', description: 'Format, mount, browse, unmount' },
  { id: 'test_08_archive_cycle', label: 'Archive', description: 'Archive → verify → catalog' },
  { id: 'test_09_restore_cycle', label: 'Restore', description: 'Restore → checksum → destination' },
  { id: 'test_10_fault_scenarios', label: 'Faults', description: 'Drive failure, jam, partial restore' },
  { id: 'test_11_diagnostics', label: 'Diagnostics', description: 'Health, events, firmware, RAS' },
  { id: 'test_12_multi_library', label: 'Multi-Library', description: 'Library switch, header routing' },
  { id: 'test_13_ui_scenarios', label: 'UI Scenarios', description: 'Operator workflow scenarios' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function statusBadge(status: RunStatus) {
  switch (status) {
    case 'queued':    return <Badge variant="gray">Queued</Badge>;
    case 'running':   return <Badge variant="blue"><Loader2 className="mr-1 h-3 w-3 animate-spin inline" />Running</Badge>;
    case 'completed': return <Badge variant="green">Passed</Badge>;
    case 'failed':    return <Badge variant="red">Failed</Badge>;
    default:          return <Badge variant="gray">{status}</Badge>;
  }
}

function LineItem({ line }: { line: string }) {
  const isPassed  = line.includes(' PASSED');
  const isFailed  = line.includes(' FAILED');
  const isError   = line.includes(' ERROR') && line.includes('::');
  const isSkipped = line.includes(' SKIPPED');
  const isSection = line.startsWith('=') || line.startsWith('-');

  return (
    <div
      className={cn(
        'font-mono text-xs leading-5 whitespace-pre-wrap break-all',
        isPassed  && 'text-green-400',
        isFailed  && 'text-red-400',
        isError   && 'text-amber-400',
        isSkipped && 'text-slate-500',
        isSection && 'text-slate-400 font-semibold',
        !isPassed && !isFailed && !isError && !isSkipped && !isSection && 'text-slate-300',
      )}
    >
      {line}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function TestRunner() {
  const [target, setTarget] = useState<TestTarget>('emulator');
  const [timingProfile, setTimingProfile] = useState<TimingProfile>('instant');
  const [i3Url, setI3Url] = useState('http://localhost:8082');
  const [i3User, setI3User] = useState('admin');
  const [i3Password, setI3Password] = useState('');
  const [showRealConfig, setShowRealConfig] = useState(false);
  const [selectedModules, setSelectedModules] = useState<Set<string>>(new Set(TEST_MODULES.map(m => m.id)));

  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<TestRunStatus | null>(null);
  const [outputLines, setOutputLines] = useState<string[]>([]);
  const [running, setRunning] = useState(false);

  const [recentRuns, setRecentRuns] = useState<TestRunSummary[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  const outputRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch recent runs on mount
  useEffect(() => {
    void listRuns().then(setRecentRuns).catch(() => {});
  }, []);

  // Auto-scroll output
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [outputLines]);

  // Poll status while running
  useEffect(() => {
    if (!activeRunId) return;
    pollRef.current = setInterval(() => {
      void getRunStatus(activeRunId).then(s => {
        setRunStatus(s);
        if (s.status === 'completed' || s.status === 'failed') {
          clearInterval(pollRef.current!);
          setRunning(false);
          void listRuns().then(setRecentRuns).catch(() => {});
        }
      }).catch(() => {});
    }, 1500);
    return () => clearInterval(pollRef.current!);
  }, [activeRunId]);

  function toggleModule(id: string) {
    setSelectedModules(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  function selectAll() { setSelectedModules(new Set(TEST_MODULES.map(m => m.id))); }
  function selectNone() { setSelectedModules(new Set()); }

  async function handleRun() {
    if (running) return;
    setRunning(true);
    setOutputLines([]);
    setRunStatus(null);
    setActiveRunId(null);
    if (esRef.current) { esRef.current.close(); esRef.current = null; }

    try {
      const resp = await startTestRun({
        target,
        timing_profile: timingProfile,
        i3_aml_url: i3Url,
        i3_aml_user: i3User,
        i3_aml_password: i3Password || 'password',
        modules: selectedModules.size === TEST_MODULES.length ? null : Array.from(selectedModules),
      });

      setActiveRunId(resp.run_id);

      esRef.current = openRunStream(
        resp.run_id,
        (line) => setOutputLines(prev => [...prev, line]),
        (status, passed, failed, total) => {
          setRunStatus(prev => prev ? { ...prev, status, passed, failed, total_tests: total } : null);
          setRunning(false);
        },
      );
    } catch (err) {
      setOutputLines([`ERROR starting test run: ${String(err)}`]);
      setRunning(false);
    }
  }

  function handleDownload() {
    if (!activeRunId) return;
    const blob = new Blob([outputLines.join('\n')], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `i3-test-run-${activeRunId}.txt`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const allSelected = selectedModules.size === TEST_MODULES.length;
  const noneSelected = selectedModules.size === 0;

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <FlaskConical className="h-6 w-6 text-quantum-red" />
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Quantum i3 Test Runner</h1>
          <p className="text-sm text-slate-400">
            Validate the emulated or physical Quantum Scalar i3 using the full i3 test suite.
          </p>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-[360px_1fr]">
        {/* --- LEFT: Configuration --- */}
        <div className="space-y-4">
          {/* Target */}
          <div className="rounded-lg border border-quantum-border bg-quantum-panel p-4 space-y-3">
            <div className="text-xs font-semibold uppercase tracking-widest text-slate-400">Target</div>
            <div className="flex gap-2">
              {(['emulator', 'real'] as TestTarget[]).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => { setTarget(t); if (t === 'real') setShowRealConfig(true); }}
                  className={cn(
                    'flex-1 rounded px-3 py-2 text-sm font-medium border transition',
                    target === t
                      ? 'border-quantum-red bg-quantum-red/20 text-white'
                      : 'border-quantum-border bg-quantum-north text-slate-300 hover:border-quantum-red/40',
                  )}
                >
                  {t === 'emulator' ? '⬡ Emulator' : '⚡ Real i3'}
                </button>
              ))}
            </div>
            {target === 'real' && (
              <div className="rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                <AlertTriangle className="mr-1 inline h-3 w-3" />
                Real i3 mode requires <code>I3_REAL_HARDWARE_ENABLED=true</code> on the API container.
              </div>
            )}
          </div>

          {/* Real i3 config */}
          {target === 'real' && (
            <div className="rounded-lg border border-quantum-border bg-quantum-panel p-4 space-y-3">
              <button
                type="button"
                onClick={() => setShowRealConfig(!showRealConfig)}
                className="flex w-full items-center justify-between text-xs font-semibold uppercase tracking-widest text-slate-400"
              >
                Real i3 Connection
                {showRealConfig ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
              </button>
              {showRealConfig && (
                <div className="space-y-2">
                  <label className="block">
                    <span className="text-xs text-slate-400">AML URL</span>
                    <input
                      className="mt-1 w-full rounded border border-quantum-border bg-quantum-north px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-quantum-red"
                      value={i3Url}
                      onChange={e => setI3Url(e.target.value)}
                      placeholder="http://192.168.1.50:8082"
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-400">Username</span>
                    <input
                      className="mt-1 w-full rounded border border-quantum-border bg-quantum-north px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-quantum-red"
                      value={i3User}
                      onChange={e => setI3User(e.target.value)}
                    />
                  </label>
                  <label className="block">
                    <span className="text-xs text-slate-400">Password</span>
                    <input
                      type="password"
                      className="mt-1 w-full rounded border border-quantum-border bg-quantum-north px-2 py-1 text-sm text-slate-200 focus:outline-none focus:border-quantum-red"
                      value={i3Password}
                      onChange={e => setI3Password(e.target.value)}
                      placeholder="••••••••"
                    />
                  </label>
                </div>
              )}
            </div>
          )}

          {/* Timing Profile */}
          <div className="rounded-lg border border-quantum-border bg-quantum-panel p-4 space-y-3">
            <div className="text-xs font-semibold uppercase tracking-widest text-slate-400">Timing Profile</div>
            {([
              { id: 'instant', label: 'Instant', desc: '0s delays — fastest CI run' },
              { id: 'realistic', label: 'Realistic', desc: '3–8s delays — feels like real hardware' },
              { id: 'hardware', label: 'Hardware', desc: '35–300s delays — real i3 tolerances' },
            ] as { id: TimingProfile; label: string; desc: string }[]).map(p => (
              <label key={p.id} className="flex cursor-pointer items-start gap-2">
                <input
                  type="radio"
                  name="timing"
                  value={p.id}
                  checked={timingProfile === p.id}
                  onChange={() => setTimingProfile(p.id)}
                  className="mt-0.5 accent-quantum-red"
                />
                <div>
                  <div className="text-sm text-slate-200">{p.label}</div>
                  <div className="text-xs text-slate-500">{p.desc}</div>
                </div>
              </label>
            ))}
          </div>

          {/* Test Modules */}
          <div className="rounded-lg border border-quantum-border bg-quantum-panel p-4 space-y-2">
            <div className="flex items-center justify-between">
              <div className="text-xs font-semibold uppercase tracking-widest text-slate-400">Test Modules</div>
              <div className="flex gap-2 text-xs text-slate-400">
                <button type="button" onClick={selectAll} className={cn('hover:text-white', allSelected && 'text-white')}>All</button>
                <span>·</span>
                <button type="button" onClick={selectNone} className={cn('hover:text-white', noneSelected && 'text-white')}>None</button>
              </div>
            </div>
            {TEST_MODULES.map(m => (
              <label key={m.id} className="flex cursor-pointer items-start gap-2 rounded px-1 py-0.5 hover:bg-quantum-north">
                <input
                  type="checkbox"
                  checked={selectedModules.has(m.id)}
                  onChange={() => toggleModule(m.id)}
                  className="mt-0.5 accent-quantum-red"
                />
                <div>
                  <span className="text-sm text-slate-200">{m.label}</span>
                  <span className="ml-2 text-xs text-slate-500">{m.description}</span>
                </div>
              </label>
            ))}
          </div>

          {/* Run button */}
          <button
            type="button"
            onClick={() => void handleRun()}
            disabled={running || noneSelected}
            className={cn(
              'flex w-full items-center justify-center gap-2 rounded-md px-4 py-3 text-sm font-semibold transition',
              running || noneSelected
                ? 'cursor-not-allowed bg-quantum-north text-slate-500'
                : 'bg-quantum-red text-white hover:bg-red-600',
            )}
          >
            {running ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Running…</>
            ) : (
              <><Play className="h-4 w-4" /> Run Tests</>
            )}
          </button>
        </div>

        {/* --- RIGHT: Output --- */}
        <div className="flex flex-col gap-4">
          {/* Status bar */}
          {runStatus && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg border border-quantum-border bg-quantum-panel px-4 py-3">
              {statusBadge(runStatus.status)}
              <span className="text-sm text-slate-300">
                <span className="text-green-400 font-semibold">{runStatus.passed}</span> passed ·{' '}
                <span className="text-red-400 font-semibold">{runStatus.failed}</span> failed ·{' '}
                <span className="text-amber-400 font-semibold">{runStatus.errors}</span> errors ·{' '}
                <span className="text-slate-500">{runStatus.skipped}</span> skipped
              </span>
              <div className="ml-auto flex items-center gap-2 text-xs text-slate-400">
                {runStatus.status === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
                {runStatus.status === 'completed' && <CheckCircle className="h-3 w-3 text-green-400" />}
                {runStatus.status === 'failed' && <XCircle className="h-3 w-3 text-red-400" />}
                Run {activeRunId}
              </div>
            </div>
          )}

          {/* Progress bar */}
          {runStatus && runStatus.total_tests > 0 && (
            <div className="h-1.5 w-full rounded-full bg-quantum-north overflow-hidden">
              <div
                className="h-full rounded-full bg-green-500 transition-all duration-300"
                style={{ width: `${Math.round(((runStatus.passed + runStatus.failed + runStatus.errors + runStatus.skipped) / Math.max(runStatus.total_tests, 1)) * 100)}%` }}
              />
            </div>
          )}

          {/* Output terminal */}
          <div
            ref={outputRef}
            className="flex-1 min-h-[400px] max-h-[600px] overflow-y-auto rounded-lg border border-quantum-border bg-[#0d1117] p-4 space-y-0.5"
          >
            {outputLines.length === 0 && !running && (
              <div className="flex h-full items-center justify-center text-slate-600 text-sm">
                Configure a target and click <strong className="mx-1 text-slate-400">Run Tests</strong> to start.
              </div>
            )}
            {outputLines.map((line, i) => (
              <LineItem key={i} line={line} />
            ))}
            {running && outputLines.length === 0 && (
              <div className="flex items-center gap-2 text-slate-500 text-xs">
                <Loader2 className="h-3 w-3 animate-spin" /> Starting test process…
              </div>
            )}
          </div>

          {/* Download */}
          {outputLines.length > 0 && (
            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleDownload}
                className="flex items-center gap-2 rounded px-3 py-1.5 text-xs text-slate-400 hover:bg-quantum-north hover:text-white transition"
              >
                <Download className="h-3 w-3" /> Download output
              </button>
            </div>
          )}

          {/* Recent runs */}
          <div className="rounded-lg border border-quantum-border bg-quantum-panel">
            <button
              type="button"
              onClick={() => setShowHistory(!showHistory)}
              className="flex w-full items-center justify-between px-4 py-3 text-xs font-semibold uppercase tracking-widest text-slate-400 hover:text-slate-200"
            >
              Recent Runs ({recentRuns.length})
              {showHistory ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            </button>
            {showHistory && (
              <div className="divide-y divide-quantum-border border-t border-quantum-border">
                {recentRuns.length === 0 && (
                  <div className="px-4 py-3 text-xs text-slate-500">No recent runs.</div>
                )}
                {recentRuns.map(r => (
                  <div key={r.run_id} className="flex items-center justify-between px-4 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      {statusBadge(r.status)}
                      <span className="text-slate-400">{r.run_id}</span>
                      <span className="text-slate-600">·</span>
                      <span className="text-slate-500">{r.target}</span>
                      <span className="text-slate-600">·</span>
                      <span className="text-slate-500">{r.timing_profile}</span>
                    </div>
                    <div className="text-slate-400">
                      <span className="text-green-400">{r.passed}</span>/{r.total_tests}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
