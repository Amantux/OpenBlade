import { rootApiRequest } from './client';

export interface TapeOperation {
  op_id: string;
  op_type: string;
  barcode: string;
  drive_id: number | null;
  slot_id: number | null;
  tape_path: string | null;
  size_bytes: number | null;
  checksum_sha256: string | null;
  requested_by: string;
  job_id: string | null;
  priority: number;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | 'skipped' | string;
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface SafetyStatus {
  code: 'SAFETY_003';
  status: 'ok' | 'warning' | 'failed';
  summary: string;
  guidance: string;
  checked_at: string;
}

export async function listTapeOperations(limit = 100): Promise<TapeOperation[]> {
  return rootApiRequest<TapeOperation[]>(`/tape-ops?limit=${limit}`);
}

export async function getSafetyStatus(): Promise<SafetyStatus> {
  return Promise.resolve({
    code: 'SAFETY_003',
    status: 'warning',
    summary: 'Last safety review requires operator confirmation before destructive tape actions.',
    guidance: 'Verify media ownership, mounted tape selection, and destination paths before proceeding.',
    checked_at: new Date().toISOString(),
  });
}

export async function runSafetyCheck(): Promise<SafetyStatus> {
  return getSafetyStatus();
}
