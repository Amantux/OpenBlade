import { rootApiRequest } from './client';

export interface LibrarySummary {
  id: number;
  name: string;
  emulator_url: string;
  serial_number: string | null;
  model: string;
  enabled: boolean;
  status: string;
  drive_count: number;
  tape_count: number;
}

export function listLibraries(): Promise<LibrarySummary[]> {
  return rootApiRequest<LibrarySummary[]>('/libraries');
}
