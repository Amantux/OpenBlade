export interface HealthResponse {
  status: string;
  backend: string;
  hostname?: string;
  firmware?: string;
  uptime?: number;
  drives?: number;
  slots_used?: number;
  slots_total?: number;
  latency_ms?: number;
}

export interface SlotResponse {
  slot_id?: number;
  id?: number;
  address?: string;
  occupied?: boolean;
  barcode: string | null;
  state?: string;
  drive_id?: number | null;
  type?: string;
  partition?: string;
}

export interface DriveResponse {
  drive_id?: number;
  id?: number;
  serialNumber?: string;
  loaded?: boolean;
  tape_loaded?: boolean;
  barcode: string | null;
  drive_state?: string;
  state?: string;
  status?: string;
  mount_state?: string;
  type?: string;
  partition?: string;
  location?: string;
  firmware?: string;
  loadCount?: number;
  errorCount?: number;
  cleaningCount?: number;
  lastCleaned?: string | null;
}

export interface InventoryResponse {
  library_id?: string;
  slots: SlotResponse[];
  drives: DriveResponse[];
  changer_state?: string;
  changer?: {
    state: string;
  };
  partitions?: string[];
}

export interface JobResponse {
  id: string;
  status: string;
  state?: string;
  job_type?: string;
  type?: string;
  priority?: string;
  created_at: string;
  updated_at: string;
  error?: string | null;
  result?: string | null;
  metadata?: Record<string, unknown>;
  bytes_written?: number;
  progress?: number;
  source_path?: string;
  barcode?: string | null;
  shard_index?: number | null;
  total_shards?: number | null;
}

export interface VolumeGroup {
  id: string;
  name: string;
  barcodes?: string[];
}

export interface CartridgeResponse {
  barcode: string;
  type: string;
  partition: string | null;
  slotAddress: string;
  state: string;
  writeProtected: boolean;
  worm: boolean;
  generations: number;
  loadCount: number;
  errorCount: number;
  lastLoaded?: string | null;
  capacityGB?: number;
  usedGB?: number;
  percentUsed?: number;
  poolName?: string | null;
}

export interface MediaPoolResponse {
  name: string;
  type: string;
  mediaCount: number;
  policy: string;
}

export interface PartitionResponse {
  id: string;
  name: string;
  status: string;
  type: string;
  driveCount: number;
  slotCount: number;
  ieSlotCount: number;
  cleaningSlots: number;
  mediaCount: number;
}

export interface EnqueuedJobResponse {
  job_id: string;
  status: string;
}

export interface ArchiveRequestPayload {
  source_path: string;
  volume_group: string;
}

export interface ShardedArchiveRequestPayload extends ArchiveRequestPayload {
  lane_barcodes: string[];
  mode: 'STRIPE' | 'BLOCK_STRIPE';
  block_size_mb: number;
}

export interface RestoreRequestPayload {
  file_id: string;
  destination_path: string;
}

export interface CatalogEntryResponse {
  id: string;
  source_path: string;
  barcode: string;
  size_bytes: number;
  checksum: string;
  strategy: string;
  shards: number;
  created_at: string;
}

export interface CatalogResponse {
  entries: CatalogEntryResponse[];
}

export interface SystemOverviewResponse {
  hostname: string;
  model: string;
  serialNumber: string;
  firmware: string;
  uptime: number;
  cpuUsage: number;
  memUsage: number;
  diskUsage: number;
}

export interface SystemDetailResponse {
  os: string;
  kernel: string;
  arch: string;
  cpuModel: string;
  cpuCount: number;
  totalMem: number;
  totalDisk: number;
  installedDate: string;
}

export interface SystemStatusResponse {
  overall: string;
  cpu: string;
  memory: string;
  disk: string;
  network: string;
  services: string;
}

export interface SystemVersionResponse {
  firmware: string;
  software: string;
  api: string;
  buildDate: string;
  buildNumber: string;
}

export interface UptimeResponse {
  seconds: number;
  formatted: string;
  bootTime: string;
}

export interface NetworkInterfaceResponse {
  name: string;
  type: string;
  ip: string;
  mask: string;
  gateway: string;
  mac: string;
  status: string;
  speed: string;
  duplex: string;
}

export interface NetworkConfigResponse {
  interfaces: NetworkInterfaceResponse[];
  dns: {
    primary: string;
    secondary: string;
    search: string[];
    domain: string;
  };
  ntp: {
    enabled: boolean;
    servers: string[];
    status: string;
    lastSync?: string | null;
  };
  hostname: string;
  domain: string;
}

export interface SystemConfigResponse {
  hostname: string;
  timezone: string;
  locale: string;
  dateFormat: string;
  temperatureUnit: string;
}

export interface FirmwarePackageResponse {
  name: string;
  version: string;
  size: number;
  uploadedAt: string;
  checksum: string;
  active: boolean;
}

export interface DiagnosticsResponse {
  timestamp?: string | null;
  status: string;
  tests: Array<{
    name: string;
    result: string;
    details?: string | null;
  }>;
}

export interface EventLogResponse {
  id: string;
  timestamp: string;
  severity: string;
  category: string;
  message: string;
}

export interface RasTicketResponse {
  id: string;
  severity: string;
  summary: string;
  status: string;
  createdAt: string;
  component?: string;
}

export interface Job {
  id: string;
  type: string;
  state: string;
  priority: string;
  source: string | null;
  destination: string | null;
  barcode: string | null;
  progress: number;
  startedAt: string;
  completedAt: string | null;
  durationSeconds: number;
  result: string | null;
}

export interface Move {
  id: string;
  source: string;
  destination: string;
  barcode: string;
  state: string;
  startedAt: string;
  completedAt: string | null;
}

export interface InventoryStatus {
  state: string;
  startTime: string | null;
  completedTime: string | null;
  progress: number;
  elementsScanned: number;
  elementsTotal: number;
  lastCompleted: string | null;
}

export interface InventoryResult {
  timestamp: string | null;
  elementsScanned: number;
  mediaFound: number;
  emptySlots: number;
  errors: string[];
}

export interface PhysicalSlot {
  address: string;
  elementType: string;
  state: string;
  barcode: string | null;
  full: boolean;
}

export interface IeStationSlot {
  id: string;
  address: string;
  state: string;
  barcode: string | null;
  type: string;
}

export interface IeStation {
  id: string;
  serialNumber: string;
  status: string;
  state: string;
  slotCount: number;
  slots: IeStationSlot[];
}

export interface RasTicket {
  id: string;
  severity: string;
  component: string;
  message: string;
  opened: string;
  state: string;
  resolution?: string | null;
  assignee?: string | null;
}

export interface Event {
  id: string;
  timestamp: string;
  severity: string;
  component: string;
  message: string;
  details: Record<string, unknown>;
}

export interface Alert {
  id: string;
  timestamp: string;
  severity: string;
  component: string;
  message: string;
  acknowledged: boolean;
}

export interface SystemHealth {
  overall: string;
  drivesOnline: number;
  drivesTotal: number;
  slotsUsed: number;
  slotsTotal: number;
  activeJobs: number;
  openTickets: number;
  uptime: number;
  uptimeFormatted: string;
  lastBackupTime: string | null;
  lastBackupStatus: string | null;
  backend: string;
  activeAlerts: number;
  componentStates: Record<string, string>;
}

export interface AuditEntry {
  timestamp: string;
  user: string;
  action: string;
  resource: string;
  result: string;
  ip?: string | null;
}

export type SystemHealthLevel = 'Healthy' | 'Degraded' | 'Critical';
