export interface HealthResponse {
  status: string;
  backend: string;
}

export interface SlotResponse {
  slot_id: number;
  occupied: boolean;
  barcode: string | null;
}

export interface DriveResponse {
  drive_id: number;
  loaded: boolean;
  barcode: string | null;
  drive_state: string;
  mount_state: string;
}

export interface InventoryResponse {
  library_id: string;
  slots: SlotResponse[];
  drives: DriveResponse[];
  changer_state: string;
}

export interface JobResponse {
  id: string;
  status: string;
  job_type: string;
  created_at: string;
  updated_at: string;
  error?: string | null;
  metadata?: Record<string, unknown>;
  bytes_written?: number;
}

export interface VolumeGroup {
  id: string;
  name: string;
  barcodes?: string[];
}

export interface CartridgeResponse {
  barcode: string;
  volume_group_id: string | null;
  capacity_bytes: number;
  used_bytes: number;
  state: string;
  formatted: boolean;
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

export type SystemHealth = 'Healthy' | 'Degraded' | 'Critical';
