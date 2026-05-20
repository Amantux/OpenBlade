import type {
  DriveResponse,
  HealthResponse,
  InventoryResponse,
  JobResponse,
  SlotResponse,
} from '../types/api';
import { getMetadataNumber, getMetadataString, toTitleCase } from './utils';

export type PanelStatus = 'good' | 'warning' | 'failed';
export type SlotTone = 'gray' | 'green' | 'blue' | 'red';

export interface NormalizedSlot {
  element: number;
  barcode: string | null;
  state: string;
  driveId: number | null;
  occupied: boolean;
  magazine: number;
  isIeArea: boolean;
  isCleaning: boolean;
}

export interface NormalizedDrive {
  id: number;
  serialNumber: string;
  state: string;
  barcode: string | null;
  tapeLoaded: boolean;
  mountState: string;
  type: string;
}

export interface SubsystemSummary {
  name: string;
  state: PanelStatus;
}

const HEALTHY_STATES = ['OK', 'HEALTHY', 'GOOD', 'READY'];
const FAILED_DRIVE_STATES = ['FAULTED', 'OFFLINE', 'FAILED'];
const WARNING_DRIVE_STATES = ['BUSY', 'MOUNTING', 'UNMOUNTING', 'LOADING', 'UNLOADING'];
const FAILED_CHANGER_STATES = ['FAULTED', 'FAILED', 'ERROR'];
const WARNING_CHANGER_STATES = ['BUSY', 'MOVING', 'SCANNING'];
const MAGAZINE_SIZE = 5;

function parseTrailingNumber(value?: string | null): number {
  if (!value) {
    return 0;
  }

  const match = value.match(/(\d+)$/);
  return match ? Number(match[1]) : 0;
}

export function getSlotElement(slot: SlotResponse): number {
  return slot.id ?? slot.slot_id ?? parseTrailingNumber(slot.address) ?? 0;
}

export function getDriveId(drive: DriveResponse): number {
  return drive.id ?? drive.drive_id ?? parseTrailingNumber(drive.serialNumber) ?? 0;
}

export function getDriveState(drive: DriveResponse): string {
  return String(drive.state ?? drive.drive_state ?? 'IDLE').toUpperCase();
}

export function isDriveLoaded(drive: DriveResponse): boolean {
  return Boolean(drive.tape_loaded ?? drive.loaded ?? drive.barcode);
}

export function getChangerState(inventory?: InventoryResponse): string {
  return String(inventory?.changer?.state ?? inventory?.changer_state ?? 'UNKNOWN').toUpperCase();
}

export function normalizeSlot(slot: SlotResponse): NormalizedSlot {
  const element = getSlotElement(slot);
  const driveId = slot.drive_id ?? null;
  const occupied = slot.occupied ?? Boolean(slot.barcode);
  const slotType = String(slot.type ?? '').toUpperCase();
  const state = String(slot.state ?? (driveId !== null ? 'IN_DRIVE' : occupied ? 'LOADED' : 'EMPTY')).toUpperCase();
  const barcode = slot.barcode ?? null;

  return {
    element,
    barcode,
    state,
    driveId,
    occupied,
    magazine: Math.max(1, Math.ceil(element / MAGAZINE_SIZE)),
    isIeArea: slotType === 'IE' || slot.address?.startsWith('0,0,') === true,
    isCleaning: /CLN|CLEAN/i.test(barcode ?? '') || state.includes('CLEAN'),
  };
}

export function normalizeDrive(drive: DriveResponse): NormalizedDrive {
  const state = getDriveState(drive);
  const serialNumber = drive.serialNumber ?? `DRV-${String(getDriveId(drive)).padStart(3, '0')}`;

  return {
    id: getDriveId(drive),
    serialNumber,
    state,
    barcode: drive.barcode ?? null,
    tapeLoaded: isDriveLoaded(drive),
    mountState: String(drive.mount_state ?? (isDriveLoaded(drive) ? 'MOUNTED' : 'EMPTY')).toUpperCase(),
    type: drive.type ?? 'UNKNOWN',
  };
}

export function getSlotTone(slot: NormalizedSlot): SlotTone {
  if (/ERROR|FAULT|JAM/.test(slot.state)) {
    return 'red';
  }
  if (slot.driveId !== null || slot.state.includes('DRIVE')) {
    return 'blue';
  }
  if (slot.occupied) {
    return 'green';
  }
  return 'gray';
}

export function getJobState(job: JobResponse): string {
  return String(job.state ?? job.status ?? 'UNKNOWN').toUpperCase();
}

export function getJobTypeLabel(job: JobResponse): string {
  return toTitleCase(job.type ?? job.job_type ?? 'archive');
}

export function getJobProgress(job: JobResponse): number {
  const candidates = [
    job.progress,
    getMetadataNumber(job, 'progress'),
    getMetadataNumber(job, 'progress_pct'),
    getMetadataNumber(job, 'percent_complete'),
  ];

  for (const candidate of candidates) {
    if (typeof candidate === 'number' && Number.isFinite(candidate)) {
      const normalized = candidate <= 1 ? candidate * 100 : candidate;
      return Math.max(0, Math.min(100, normalized));
    }
  }

  switch (getJobState(job)) {
    case 'COMPLETED':
      return 100;
    case 'RUNNING':
      return 55;
    case 'PENDING':
      return 5;
    default:
      return 0;
  }
}

export function getJobSourcePath(job: JobResponse): string {
  return (
    job.source_path ??
    getMetadataString(job, 'source_path') ??
    getMetadataString(job, 'path') ??
    getMetadataString(job, 'source') ??
    '—'
  );
}

export function getJobBarcode(job: JobResponse): string {
  return (
    job.barcode ??
    getMetadataString(job, 'barcode') ??
    getMetadataString(job, 'target_barcode') ??
    getMetadataString(job, 'volume_barcode') ??
    '—'
  );
}

export function getJobStrategy(job: JobResponse): string {
  return (
    getMetadataString(job, 'strategy') ??
    getMetadataString(job, 'mode') ??
    (getJobTypeLabel(job).includes('Stripe') ? getJobTypeLabel(job) : 'Single Drive')
  );
}

export function getJobShardText(job: JobResponse): string {
  const total = job.total_shards ?? getMetadataNumber(job, 'total_shards');
  const index = job.shard_index ?? getMetadataNumber(job, 'shard_index');
  const lanes = job.metadata?.lane_barcodes;

  if (typeof total === 'number' && total > 0) {
    return typeof index === 'number' ? `${index + 1}/${total}` : `${total}`;
  }
  if (Array.isArray(lanes)) {
    return String(lanes.length);
  }
  return '1';
}

export function buildSubsystemStatuses(
  health?: HealthResponse,
  inventory?: InventoryResponse,
): SubsystemSummary[] {
  const drives = inventory?.drives?.map(normalizeDrive) ?? [];
  const slots = inventory?.slots?.map(normalizeSlot) ?? [];
  const changerState = getChangerState(inventory);
  const healthState = String(health?.status ?? 'UNKNOWN').toUpperCase();
  const latency = health?.latency_ms ?? 0;

  const robot: PanelStatus = FAILED_CHANGER_STATES.includes(changerState)
    ? 'failed'
    : WARNING_CHANGER_STATES.includes(changerState)
      ? 'warning'
      : 'good';

  const driveSummary: PanelStatus = drives.some((drive) => FAILED_DRIVE_STATES.includes(drive.state))
    ? 'failed'
    : drives.some((drive) => WARNING_DRIVE_STATES.includes(drive.state) || WARNING_DRIVE_STATES.includes(drive.mountState))
      ? 'warning'
      : 'good';

  const mediaSummary: PanelStatus = slots.some((slot) => getSlotTone(slot) === 'red')
    ? 'failed'
    : slots.filter((slot) => slot.occupied).length === 0
      ? 'warning'
      : 'good';

  const powerSummary: PanelStatus = HEALTHY_STATES.includes(healthState)
    ? 'good'
    : health
      ? 'warning'
      : 'failed';

  const connectivitySummary: PanelStatus = !health
    ? 'failed'
    : !HEALTHY_STATES.includes(healthState)
      ? 'failed'
      : latency > 300
        ? 'warning'
        : 'good';

  return [
    { name: 'Robot', state: robot },
    { name: 'Drives', state: driveSummary },
    { name: 'Media', state: mediaSummary },
    { name: 'Power', state: powerSummary },
    { name: 'Connectivity', state: connectivitySummary },
  ];
}

