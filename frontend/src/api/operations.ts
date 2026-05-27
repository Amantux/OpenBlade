import type {
  IeStation,
  InventoryResult,
  InventoryStatus,
  Job,
  Move,
  PhysicalSlot,
} from '../types/api';
import { apiRequest } from './client';

export interface OperationJobReceipt {
  jobId: string;
  job?: Job;
  move?: Move;
}

interface JobResource {
  id: string;
  type: string;
  status: string;
  priority: string;
  startTime: string;
  completedTime?: string | null;
  progress?: number;
  result?: string | null;
}

interface JobListEnvelope {
  jobList: {
    job: JobResource[];
  };
}

interface MoveResource {
  id: string;
  source: string;
  destination: string;
  barcode: string;
  status: string;
  startTime: string;
  completedTime?: string | null;
}

interface MoveListEnvelope {
  moveList: {
    move: MoveResource[];
  };
}

interface InventoryStatusEnvelope {
  inventoryStatus: {
    state: string;
    startTime?: string | null;
    completedTime?: string | null;
    progress?: number;
    elementsScanned?: number;
    elementsTotal?: number;
  };
}

interface InventoryResultEnvelope {
  inventoryResult: InventoryResult;
}

interface OperationStateEnvelope {
  importStatus?: { state: string };
  exportStatus?: { state: string };
  cleaningStatus?: { state: string; drives?: string[] };
}

interface PhysicalElementsEnvelope {
  elementList: {
    element: Array<{
      address: number | string;
      type: string;
      state: string;
      barcode?: string | null;
    }>;
  };
}

interface PartitionEnvelope {
  partitionList: {
    partition: Array<{
      name: string;
      slotCount: number;
      mediaCount: number;
    }>;
  };
}

interface DriveEnvelope {
  driveList: {
    drive: Array<{
      serialNumber: string;
    }>;
  };
}

interface IeStationsEnvelope {
  ieStationList: {
    ieStation: IeStation[];
  };
}

function normalizeState(status: string): string {
  switch (String(status).toUpperCase()) {
    case 'ACTIVE':
      return 'RUNNING';
    case 'PAUSED':
      return 'PENDING';
    default:
      return String(status).toUpperCase();
  }
}

function durationSeconds(startedAt: string, completedAt?: string | null): number {
  const started = new Date(startedAt).getTime();
  const finished = completedAt ? new Date(completedAt).getTime() : Date.now();
  if (Number.isNaN(started) || Number.isNaN(finished)) {
    return 0;
  }
  return Math.max(Math.round((finished - started) / 1000), 0);
}

function mapMove(move: MoveResource): Move {
  return {
    id: move.id,
    source: move.source,
    destination: move.destination,
    barcode: move.barcode,
    state: normalizeState(move.status),
    startedAt: move.startTime,
    completedAt: move.completedTime ?? null,
  };
}

function mapJob(job: JobResource, movesById: Map<string, Move>): Job {
  const relatedMove = movesById.get(job.id);
  const state = normalizeState(job.status);
  return {
    id: job.id,
    type: job.type,
    state,
    priority: job.priority,
    source: relatedMove?.source ?? null,
    destination: relatedMove?.destination ?? null,
    barcode: relatedMove?.barcode ?? null,
    progress: job.progress ?? (state === 'COMPLETED' ? 100 : 0),
    startedAt: job.startTime,
    completedAt: job.completedTime ?? null,
    durationSeconds: durationSeconds(job.startTime, job.completedTime),
    result:
      state === 'COMPLETED'
        ? 'SUCCESS'
        : state === 'FAILED'
          ? 'FAILED'
          : state === 'CANCELLED'
            ? 'CANCELLED'
            : job.result ?? null,
    library_id: null,
  };
}

function newestByTime<T extends { startedAt: string }>(items: T[]): T | undefined {
  return [...items].sort((left, right) => right.startedAt.localeCompare(left.startedAt))[0];
}

async function createOperationJob(path: string, body: unknown, expectedType: string): Promise<OperationJobReceipt> {
  const before = await listActiveJobs().catch(() => [] as Job[]);
  await apiRequest(path, { method: 'POST', body });
  const after = await listActiveJobs().catch(() => [] as Job[]);
  const beforeIds = new Set(before.map((job) => job.id));
  const created = after.filter((job) => !beforeIds.has(job.id) && job.type === expectedType);
  const job = newestByTime(created) ?? newestByTime(after.filter((item) => item.type === expectedType));
  return {
    jobId: job?.id ?? '',
    job,
  };
}

export async function listMoves(): Promise<Move[]> {
  const response = await apiRequest<MoveListEnvelope>('/moves');
  return response.moveList.move.map(mapMove);
}

export async function listActiveJobs(): Promise<Job[]> {
  const [jobsResponse, moves] = await Promise.all([
    apiRequest<JobListEnvelope>('/jobs'),
    listMoves().catch(() => []),
  ]);
  const movesById = new Map(moves.map((move) => [move.id, move]));
  return jobsResponse.jobList.job.map((job) => mapJob(job, movesById));
}

export async function listJobHistory(): Promise<Job[]> {
  const response = await apiRequest<JobListEnvelope>('/jobs/history');
  return response.jobList.job.map((job) => mapJob(job, new Map()));
}

export async function cancelJob(jobId: string): Promise<void> {
  await apiRequest(`/job/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
}

export async function listPhysicalSlots(): Promise<PhysicalSlot[]> {
  const response = await apiRequest<PhysicalElementsEnvelope>('/physicalLibrary/elements');
  return response.elementList.element.map((element) => {
    const normalizedState = String(element.state).toLowerCase();
    return {
      address: element.address,
      elementType: element.type,
      state: element.state,
      barcode: element.barcode ?? null,
      full: Boolean(element.barcode) || ['occupied', 'loaded', 'full', 'mounted', 'in_drive'].includes(normalizedState),
    };
  });
}

export async function createMove(sourceSlot: string | number, destSlot: string | number, barcode?: string): Promise<OperationJobReceipt> {
  const normalizedSourceSlot = String(sourceSlot);
  const normalizedDestSlot = String(destSlot);
  const resolvedBarcode = barcode ?? (await listPhysicalSlots()).find((slot) => String(slot.address) === normalizedSourceSlot)?.barcode;
  if (!resolvedBarcode) {
    throw new Error(`No barcode found in source slot ${normalizedSourceSlot}.`);
  }

  const beforeMoves = await listMoves().catch(() => [] as Move[]);
  const receipt = await createOperationJob(
    '/move',
    {
      move: {
        source: normalizedSourceSlot,
        destination: normalizedDestSlot,
        barcode: resolvedBarcode,
      },
    },
    'move',
  );
  const afterMoves = await listMoves().catch(() => [] as Move[]);
  const beforeIds = new Set(beforeMoves.map((move) => move.id));
  const move = newestByTime(afterMoves.filter((item) => !beforeIds.has(item.id))) ?? newestByTime(afterMoves);

  return {
    ...receipt,
    jobId: move?.id ?? receipt.jobId,
    move,
  };
}

export async function runInventory(): Promise<void> {
  await apiRequest('/inventory', { method: 'POST' });
}

export async function getInventoryStatus(): Promise<InventoryStatus> {
  const response = await apiRequest<InventoryStatusEnvelope>('/inventory/status');
  return {
    state: response.inventoryStatus.state,
    startTime: response.inventoryStatus.startTime ?? null,
    completedTime: response.inventoryStatus.completedTime ?? null,
    progress: response.inventoryStatus.progress ?? 0,
    elementsScanned: response.inventoryStatus.elementsScanned ?? 0,
    elementsTotal: response.inventoryStatus.elementsTotal ?? 0,
    lastCompleted: response.inventoryStatus.completedTime ?? null,
  };
}

export async function getInventoryResult(): Promise<InventoryResult> {
  const response = await apiRequest<InventoryResultEnvelope>('/inventory/results');
  return response.inventoryResult;
}

export async function getImportStatus(): Promise<{ state: string }> {
  const response = await apiRequest<OperationStateEnvelope>('/import/status');
  return { state: response.importStatus?.state ?? 'unknown' };
}

export async function getExportStatus(): Promise<{ state: string }> {
  const response = await apiRequest<OperationStateEnvelope>('/export/status');
  return { state: response.exportStatus?.state ?? 'unknown' };
}

export async function listIeStations(): Promise<IeStation[]> {
  const response = await apiRequest<IeStationsEnvelope>('/ieStations');
  return response.ieStationList.ieStation;
}

async function getFirstPartitionName(): Promise<string> {
  const response = await apiRequest<PartitionEnvelope>('/partitions');
  const partition = response.partitionList.partition[0];
  if (!partition) {
    throw new Error('No partitions available for the requested operation.');
  }
  return partition.name;
}

async function getPrimaryIeStationId(): Promise<string> {
  const stations = await listIeStations();
  const station = stations[0];
  if (!station) {
    throw new Error('No import/export stations are available.');
  }
  return station.id;
}

export async function startImport(stationId?: string): Promise<void> {
  const [partition, ieStation] = await Promise.all([getFirstPartitionName(), stationId ? Promise.resolve(stationId) : getPrimaryIeStationId()]);
  await apiRequest('/import', {
    method: 'POST',
    body: {
      import: {
        partition,
        ieStation,
      },
    },
  });
}

export async function startExport(stationId?: string, requestedBarcode?: string): Promise<void> {
  const [ieStation, slots] = await Promise.all([stationId ? Promise.resolve(stationId) : getPrimaryIeStationId(), listPhysicalSlots()]);
  const barcode = requestedBarcode ?? slots.find((slot) => slot.elementType === 'slot' && slot.full && slot.barcode)?.barcode;
  if (!barcode) {
    throw new Error('No cartridge is available to export.');
  }

  await apiRequest('/export', {
    method: 'POST',
    body: {
      export: {
        barcodes: [barcode],
        ieStation,
      },
    },
  });
}

export async function getCleaningStatus(): Promise<{ state: string; drives: string[] }> {
  const response = await apiRequest<OperationStateEnvelope>('/operations/cleaning/status');
  return {
    state: response.cleaningStatus?.state ?? 'unknown',
    drives: response.cleaningStatus?.drives ?? [],
  };
}

export async function startCleaning(): Promise<void> {
  const response = await apiRequest<DriveEnvelope>('/drives');
  const drives = response.driveList.drive.map((drive) => drive.serialNumber);
  if (drives.length === 0) {
    throw new Error('No drives are available for cleaning.');
  }

  await apiRequest('/operations/cleaning', {
    method: 'POST',
    body: { drives },
  });
}

export function queueDriveCleaning(drives: string[]): Promise<OperationJobReceipt> {
  return createOperationJob('/operations/clean', { clean: { drives } }, 'clean');
}

export function queueDriveIdentify(): Promise<OperationJobReceipt> {
  return createOperationJob('/operations/audit', {}, 'audit');
}

export function queueDrivePowerCycle(): Promise<OperationJobReceipt> {
  return createOperationJob('/operations/calibrate', {}, 'calibrate');
}

export function queueDrivePerformanceTest(barcodes: string[]): Promise<OperationJobReceipt> {
  return createOperationJob('/operations/verify', { verify: { barcodes } }, 'verify');
}

export async function openIeDoor(stationId?: string): Promise<void> {
  const id = stationId ?? (await getPrimaryIeStationId());
  await apiRequest(`/ieStation/${encodeURIComponent(id)}/open`, { method: 'POST' });
}

export async function closeIeDoor(stationId?: string): Promise<void> {
  const id = stationId ?? (await getPrimaryIeStationId());
  await apiRequest(`/ieStation/${encodeURIComponent(id)}/close`, { method: 'POST' });
}
