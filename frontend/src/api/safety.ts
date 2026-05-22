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

export interface SafetyCheckItem {
  name: string;
  status: 'ok' | 'warning' | 'failed';
  message: string;
}

export interface SafetyStatus {
  status: 'ok' | 'warning' | 'failed';
  checks: SafetyCheckItem[];
}

export async function listTapeOperations(limit = 100): Promise<TapeOperation[]> {
  return rootApiRequest<TapeOperation[]>(`/tape-ops?limit=${limit}`);
}

export async function getSafetyStatus(): Promise<SafetyStatus> {
  return rootApiRequest<SafetyStatus>('/safety/check');
}

export async function runSafetyCheck(): Promise<SafetyStatus> {
  return rootApiRequest<SafetyStatus>('/safety/check', { method: 'POST' });
}
