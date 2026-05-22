import { rootApiRequest } from './client';

export interface LibrarySummary {
  id: number;
  name: string;
  emulator_url: string;
  serial_number: string | null;
  model: string;
  enabled: boolean;
  role: string;
  sort_order: number;
  status: string;
  drive_count: number;
  tape_count: number;
  active_job_count: number;
  slot_count: number;
  occupied_slot_count: number;
  slot_utilization_percent: number;
  alerts_count: number;
  response_ms: number | null;
  last_seen_at: string | null;
}

export interface LibraryPayload {
  name: string;
  emulator_url: string;
  serial_number?: string | null;
  model?: string;
  role?: string;
  sort_order?: number;
  enabled?: boolean;
}

export function listLibraries(): Promise<LibrarySummary[]> {
  return rootApiRequest<LibrarySummary[]>('/libraries');
}

export function createLibrary(data: LibraryPayload): Promise<LibrarySummary> {
  return rootApiRequest<LibrarySummary>('/libraries', {
    method: 'POST',
    body: data,
  });
}

export function updateLibrary(id: number, data: LibraryPayload): Promise<LibrarySummary> {
  return rootApiRequest<LibrarySummary>(`/libraries/${id}`, {
    method: 'PUT',
    body: data,
  });
}

export function deleteLibrary(id: number): Promise<{ deleted: number }> {
  return rootApiRequest<{ deleted: number }>(`/libraries/${id}`, {
    method: 'DELETE',
  });
}
