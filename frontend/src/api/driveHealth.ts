import { rootApiRequest } from './client';

export type TapeAlertSeverity = 'critical' | 'warning' | 'information' | 'unknown';

export interface DriveInquiry {
  deviceType: string;
  vendor: string;
  product: string;
  revision: string;
  /** Empty when the drive reports no unit serial number. */
  serial: string;
}

export interface TapeAlertFlag {
  /** Null when sg_logs named a flag the backend's spec table does not classify. */
  number: number | null;
  name: string;
  severity: TapeAlertSeverity;
}

export interface DriveHealth {
  device: string;
  inquiry: DriveInquiry;
  tapeAlertSupported: boolean;
  tapeAlertReason: string | null;
  worstSeverity: TapeAlertSeverity | null;
  flagsRead: number;
  activeFlags: TapeAlertFlag[];
}

export interface DriveHealthListing {
  drives: DriveHealth[];
}

/**
 * Per-drive inquiry plus TapeAlert flags.
 *
 * Answers 503 unless the backend runs real hardware — the simulator has no SCSI
 * drive to interrogate, and the detail names the two variables that enable it.
 */
export function getDriveHealth(device?: string): Promise<DriveHealthListing> {
  const query = device ? `?device=${encodeURIComponent(device)}` : '';
  return rootApiRequest<DriveHealthListing>(`/hardware/drive-health${query}`);
}
