/**
 * testRunner.ts — Typed API client for the OpenBlade Test Runner.
 */
import { rootApiRequest } from './client';

export type TimingProfile = 'instant' | 'realistic' | 'hardware';
export type TestTarget = 'emulator' | 'real';
export type RunStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface TestRunRequest {
  target: TestTarget;
  timing_profile: TimingProfile;
  i3_aml_url?: string;
  i3_aml_user?: string;
  i3_aml_password?: string;
  modules?: string[] | null;
}

export interface TestRunResponse {
  run_id: string;
  status: RunStatus;
  started_at: string;
  target: TestTarget;
  timing_profile: TimingProfile;
}

export interface TestRunStatus {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  target: TestTarget;
  timing_profile: TimingProfile;
  total_tests: number;
  passed: number;
  failed: number;
  errors: number;
  skipped: number;
  exit_code: number | null;
}

export interface TestRunSummary {
  run_id: string;
  status: RunStatus;
  started_at: string;
  finished_at: string | null;
  target: TestTarget;
  timing_profile: TimingProfile;
  passed: number;
  failed: number;
  total_tests: number;
}

export async function startTestRun(req: TestRunRequest): Promise<TestRunResponse> {
  return rootApiRequest<TestRunResponse>('/test-runner/run', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function getRunStatus(runId: string): Promise<TestRunStatus> {
  return rootApiRequest<TestRunStatus>(`/test-runner/status/${runId}`);
}

export async function listRuns(): Promise<TestRunSummary[]> {
  return rootApiRequest<TestRunSummary[]>('/test-runner/runs');
}

/** Open an EventSource stream for live test output. Returns the EventSource instance. */
export function openRunStream(runId: string, onLine: (line: string, idx: number) => void, onDone: (status: RunStatus, passed: number, failed: number, total: number) => void): EventSource {
  const url = `/api/test-runner/stream/${runId}`;
  const es = new EventSource(url);
  es.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data as string) as { line?: string; idx?: number; event?: string; status?: RunStatus; passed?: number; failed?: number; total?: number };
      if (data.event === 'done') {
        onDone(data.status ?? 'completed', data.passed ?? 0, data.failed ?? 0, data.total ?? 0);
        es.close();
      } else if (data.line !== undefined) {
        onLine(data.line, data.idx ?? 0);
      }
    } catch {
      // ignore parse errors
    }
  };
  es.onerror = () => {
    es.close();
  };
  return es;
}
