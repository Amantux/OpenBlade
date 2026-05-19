import type {
  DriveResponse,
  HealthResponse,
  JobResponse,
  SystemHealth,
} from '../types/api';

export type BadgeVariant = 'gray' | 'blue' | 'green' | 'amber' | 'red' | 'redDim';

export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(' ');
}

export function toTitleCase(value: string): string {
  return value
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\w/g, (char) => char.toUpperCase());
}

export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return '0 B';
  }

  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** exponent;
  return `${amount.toFixed(amount >= 100 || exponent === 0 ? 0 : 1)} ${units[exponent]}`;
}

export function formatDate(value: string): string {
  if (!value) {
    return '—';
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(parsed);
}

export function deriveSystemHealth(
  health?: HealthResponse,
  drives: DriveResponse[] = [],
  changerState?: string,
): SystemHealth {
  if (!health || !['ok', 'healthy'].includes(health.status.toLowerCase())) {
    return 'Critical';
  }

  if (drives.some((drive) => ['FAULTED', 'OFFLINE'].includes(String(drive.drive_state ?? drive.state ?? '').toUpperCase()))) {
    return 'Critical';
  }

  if (
    (changerState && !['IDLE', 'READY'].includes(changerState.toUpperCase())) ||
    drives.some((drive) => ['BUSY'].includes(String(drive.drive_state ?? drive.state ?? '').toUpperCase())) ||
    drives.some((drive) => ['MOUNTING', 'UNMOUNTING'].includes(String(drive.mount_state ?? '').toUpperCase()))
  ) {
    return 'Degraded';
  }

  return 'Healthy';
}

export function getDriveStateVariant(state: string): BadgeVariant {
  switch (state.toUpperCase()) {
    case 'BUSY':
      return 'blue';
    case 'FAULTED':
      return 'red';
    case 'OFFLINE':
      return 'redDim';
    default:
      return 'gray';
  }
}

export function getMountStateVariant(state: string): BadgeVariant {
  switch (state.toUpperCase()) {
    case 'MOUNTED':
      return 'green';
    case 'MOUNTING':
    case 'UNMOUNTING':
      return 'amber';
    default:
      return 'gray';
  }
}

export function getJobStatusVariant(status: string): BadgeVariant {
  switch (status.toUpperCase()) {
    case 'RUNNING':
      return 'blue';
    case 'COMPLETED':
      return 'green';
    case 'FAILED':
      return 'red';
    case 'CANCELLED':
      return 'gray';
    default:
      return 'gray';
  }
}

function getMetadataValue(job: JobResponse, key: string): unknown {
  return job.metadata ? job.metadata[key] : undefined;
}

export function getMetadataString(job: JobResponse, key: string): string | undefined {
  const value = getMetadataValue(job, key);
  return typeof value === 'string' ? value : undefined;
}

export function getMetadataNumber(job: JobResponse, key: string): number | undefined {
  const value = getMetadataValue(job, key);
  return typeof value === 'number' ? value : undefined;
}

export function getJobPhase(job: JobResponse): string {
  const explicitPhase =
    getMetadataString(job, 'phase') ?? getMetadataString(job, 'stage') ?? getMetadataString(job, 'step');
  if (explicitPhase) {
    const candidate = toTitleCase(explicitPhase);
    if (candidate === 'Completed') {
      return 'Done';
    }
    return candidate;
  }

  switch (job.status.toUpperCase()) {
    case 'PENDING':
      return 'Pending';
    case 'RUNNING':
      return 'Writing';
    case 'COMPLETED':
      return 'Done';
    case 'FAILED':
    case 'CANCELLED':
      return 'Finalizing';
    default:
      return 'Pending';
  }
}

export function getThroughput(job: JobResponse): string | null {
  const throughput = getMetadataNumber(job, 'throughput_mb_s');
  if (throughput && throughput > 0) {
    return `${throughput.toFixed(1)} MB/s`;
  }

  const bytesWritten = job.bytes_written ?? getMetadataNumber(job, 'bytes_written');
  const elapsedSeconds = getMetadataNumber(job, 'elapsed_seconds');
  if (bytesWritten && elapsedSeconds && elapsedSeconds > 0) {
    return `${(bytesWritten / 1024 / 1024 / elapsedSeconds).toFixed(1)} MB/s`;
  }

  return null;
}
