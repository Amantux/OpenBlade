import type { DriveResponse, InventoryResponse, SlotResponse } from '../types/api';
import { apiRequest } from './client';

interface AmlDriveResource {
  serialNumber: string;
  type: string;
  status: string;
  state: string;
  partition: string;
  location: string;
  firmware: string;
  loadCount: number;
  errorCount: number;
  cleaningCount: number;
  lastCleaned?: string | null;
  loadedMedia?: {
    barcode?: string | null;
  } | null;
}

interface AmlDriveListResponse {
  driveList: {
    drive: AmlDriveResource[];
  };
}

interface AmlPartitionResource {
  name: string;
}

interface AmlPartitionListResponse {
  partitionList: {
    partition: AmlPartitionResource[];
  };
}

interface AmlSlotResource {
  id: string;
  address: string;
  state: string;
  barcode?: string | null;
  type: string;
}

interface AmlSlotListResponse {
  slotList: {
    slot: AmlSlotResource[];
  };
}

interface AmlPhysicalStatusResponse {
  physicalStatus: {
    overall: string;
  };
}

function deriveChangerState(status: string): string {
  switch (status.toLowerCase()) {
    case 'good':
      return 'READY';
    case 'warning':
      return 'BUSY';
    default:
      return 'FAILED';
  }
}

function driveIdFromSerial(serialNumber: string): number {
  const match = serialNumber.match(/(\d+)$/);
  return match ? Number(match[1]) : 0;
}

function mapDrive(drive: AmlDriveResource): DriveResponse {
  const barcode = drive.loadedMedia?.barcode ?? null;

  return {
    id: driveIdFromSerial(drive.serialNumber),
    serialNumber: drive.serialNumber,
    barcode,
    loaded: Boolean(barcode),
    tape_loaded: Boolean(barcode),
    drive_state: drive.state,
    state: drive.state,
    status: drive.status,
    mount_state: barcode ? 'MOUNTED' : 'EMPTY',
    type: drive.type,
    partition: drive.partition,
    location: drive.location,
    firmware: drive.firmware,
    loadCount: drive.loadCount,
    errorCount: drive.errorCount,
    cleaningCount: drive.cleaningCount,
    lastCleaned: drive.lastCleaned,
  };
}

function mapSlots(partition: string, slots: AmlSlotResource[], startIndex: number): SlotResponse[] {
  return slots.map((slot, index) => ({
    id: startIndex + index + 1,
    address: slot.address,
    barcode: slot.barcode ?? null,
    occupied: slot.state.toLowerCase() !== 'empty' && Boolean(slot.barcode),
    state: slot.state,
    type: slot.type,
    partition,
  }));
}

export async function getInventory(): Promise<InventoryResponse> {
  const [drivesResponse, partitionsResponse, physicalStatusResponse] = await Promise.all([
    apiRequest<AmlDriveListResponse>('/drives'),
    apiRequest<AmlPartitionListResponse>('/partitions'),
    apiRequest<AmlPhysicalStatusResponse>('/physical/status'),
  ]);

  const partitions = partitionsResponse.partitionList.partition;
  const slotResponses = await Promise.all(
    partitions.flatMap((partition) => [
      apiRequest<AmlSlotListResponse>(`/partition/${encodeURIComponent(partition.name)}/slots`),
      apiRequest<AmlSlotListResponse>(`/partition/${encodeURIComponent(partition.name)}/ieSlots`),
    ]),
  );

  const slots: SlotResponse[] = [];
  let offset = 0;

  for (let index = 0; index < partitions.length; index += 1) {
    const partition = partitions[index];
    const slotSet = slotResponses[index * 2]?.slotList.slot ?? [];
    const ieSlotSet = slotResponses[index * 2 + 1]?.slotList.slot ?? [];
    const mappedSlots = mapSlots(partition.name, slotSet, offset);
    offset += mappedSlots.length;
    const mappedIeSlots = mapSlots(partition.name, ieSlotSet, offset);
    offset += mappedIeSlots.length;
    slots.push(...mappedSlots, ...mappedIeSlots);
  }

  const changerState = deriveChangerState(physicalStatusResponse.physicalStatus.overall);

  return {
    library_id: 'OpenBlade Scalar i3',
    slots,
    drives: drivesResponse.driveList.drive.map(mapDrive),
    changer_state: changerState,
    changer: { state: changerState },
    partitions: partitions.map((partition) => partition.name),
  };
}

export async function getDrives(): Promise<DriveResponse[]> {
  const response = await apiRequest<AmlDriveListResponse>('/drives');
  return response.driveList.drive.map(mapDrive);
}
