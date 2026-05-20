import type { LibraryEntry } from './libraryStore';

export interface LibraryStatus {
  id: string;
  name: string;
  host: string;
  status: 'online' | 'offline' | 'error';
  systemInfo?: { hostname: string; version: string; uptime: number };
  health?: { slotsTotal: number; slotsUsed: number; drivesOnline: number; activeJobs: number };
  error?: string;
}

interface SystemOverviewResponse {
  systemInfo: {
    hostname: string;
    firmware: string;
    uptime: number;
  };
}

interface VersionInfoResponse {
  versionInfo: {
    firmware?: string;
    software?: string;
  };
}

interface SystemDetailResponse {
  systemDetail: {
    os: string;
  };
}

interface DriveResource {
  state?: string;
  status?: string;
}

interface DriveListResponse {
  driveList: {
    drive: DriveResource[];
  };
}

interface JobResource {
  status?: string;
}

interface JobListResponse {
  jobList: {
    job: JobResource[];
  };
}

interface PartitionResource {
  name: string;
}

interface PartitionListResponse {
  partitionList: {
    partition: PartitionResource[];
  };
}

interface SlotResource {
  barcode?: string | null;
  state?: string;
}

interface SlotListResponse {
  slotList: {
    slot: SlotResource[];
  };
}

class LibraryRequestError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'LibraryRequestError';
    this.status = status;
  }
}

function buildUrl(entry: LibraryEntry, path: string): string {
  return entry.isLocal ? `/aml${path}` : `http://${entry.host}:${entry.port}/aml${path}`;
}

function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: 'include',
    headers: new Headers(init?.headers),
  });

  const text = await response.text();
  const payload = text ? safeJsonParse(text) : null;

  if (!response.ok) {
    const detail =
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String(payload.detail)
        : text || `${response.status} ${response.statusText}`;
    throw new LibraryRequestError(detail, response.status);
  }

  return payload as T;
}

function countOnlineDrives(drives: DriveResource[]): number {
  return drives.filter((drive) => {
    const state = String(drive.state ?? drive.status ?? '').toUpperCase();
    return !['FAILED', 'FAULTED', 'OFFLINE'].includes(state);
  }).length;
}

function countActiveJobs(jobs: JobResource[]): number {
  return jobs.filter((job) => ['PENDING', 'RUNNING'].includes(String(job.status ?? '').toUpperCase())).length;
}

function countUsedSlots(slots: SlotResource[]): number {
  return slots.filter((slot) => {
    if (slot.barcode) {
      return true;
    }

    return String(slot.state ?? '').toLowerCase() !== 'empty';
  }).length;
}

async function fetchSlotSummary(entry: LibraryEntry): Promise<{ slotsTotal: number; slotsUsed: number }> {
  const partitions = await fetchJson<PartitionListResponse>(buildUrl(entry, '/partitions'));
  const names = partitions.partitionList.partition.map((partition) => partition.name);

  const slotGroups = await Promise.all(
    names.flatMap((name) => [
      fetchJson<SlotListResponse>(buildUrl(entry, `/partition/${encodeURIComponent(name)}/slots`)),
      fetchJson<SlotListResponse>(buildUrl(entry, `/partition/${encodeURIComponent(name)}/ieSlots`)),
    ]),
  );

  const slots = slotGroups.flatMap((group) => group.slotList.slot);
  return {
    slotsTotal: slots.length,
    slotsUsed: countUsedSlots(slots),
  };
}

async function loginRemote(entry: LibraryEntry): Promise<void> {
  await fetchJson<{ summary: string }>(buildUrl(entry, '/users/login'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: entry.username, password: entry.password }),
  });
}

function toFailureStatus(entry: LibraryEntry, error: unknown): LibraryStatus {
  if (error instanceof TypeError) {
    return {
      id: entry.id,
      name: entry.name,
      host: entry.host,
      status: 'offline',
      error: 'Network error while contacting library.',
    };
  }

  if (error instanceof LibraryRequestError) {
    return {
      id: entry.id,
      name: entry.name,
      host: entry.host,
      status: 'error',
      error: error.message,
    };
  }

  return {
    id: entry.id,
    name: entry.name,
    host: entry.host,
    status: 'error',
    error: error instanceof Error ? error.message : 'Unknown error',
  };
}

export async function probeLibrary(entry: LibraryEntry): Promise<LibraryStatus> {
  try {
    if (!entry.isLocal) {
      await loginRemote(entry);
    }

    const [systemResult, detailResult, versionResult, drivesResult, jobsResult, slotsResult] = await Promise.allSettled([
      fetchJson<SystemOverviewResponse>(buildUrl(entry, '/system')),
      fetchJson<SystemDetailResponse>(buildUrl(entry, '/system/info')),
      fetchJson<VersionInfoResponse>(buildUrl(entry, '/system/version')),
      fetchJson<DriveListResponse>(buildUrl(entry, '/drives')),
      fetchJson<JobListResponse>(buildUrl(entry, '/jobs')),
      fetchSlotSummary(entry),
    ]);

    if (systemResult.status === 'rejected') {
      throw systemResult.reason;
    }

    void detailResult;

    const version =
      versionResult.status === 'fulfilled'
        ? versionResult.value.versionInfo.software ?? versionResult.value.versionInfo.firmware ?? systemResult.value.systemInfo.firmware
        : systemResult.value.systemInfo.firmware;
    const drives = drivesResult.status === 'fulfilled' ? drivesResult.value.driveList.drive : [];
    const jobs = jobsResult.status === 'fulfilled' ? jobsResult.value.jobList.job : [];
    const slots = slotsResult.status === 'fulfilled' ? slotsResult.value : { slotsTotal: 0, slotsUsed: 0 };

    return {
      id: entry.id,
      name: entry.name,
      host: entry.host,
      status: 'online',
      systemInfo: {
        hostname: systemResult.value.systemInfo.hostname,
        version,
        uptime: systemResult.value.systemInfo.uptime,
      },
      health: {
        slotsTotal: slots.slotsTotal,
        slotsUsed: slots.slotsUsed,
        drivesOnline: countOnlineDrives(drives),
        activeJobs: countActiveJobs(jobs),
      },
      error: undefined,
    };
  } catch (error) {
    return toFailureStatus(entry, error);
  }
}
