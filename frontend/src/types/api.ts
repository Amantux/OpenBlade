export interface HealthResponse {
  status: string;
  backend: string;
  drives?: number;
  slots_used?: number;
  slots_total?: number;
  latency_ms?: number;
}

export interface SlotResponse {
  slot_id?: number;
  id?: number;
  occupied?: boolean;
  barcode: string | null;
  state?: string;
  drive_id?: number | null;
}

export interface DriveResponse {
  drive_id?: number;
  id?: number;
  loaded?: boolean;
  tape_loaded?: boolean;
  barcode: string | null;
  drive_state?: string;
  state?: string;
  mount_state?: string;
  type?: string;
}

export interface InventoryResponse {
  library_id?: string;
  slots: SlotResponse[];
  drives: DriveResponse[];
  changer_state?: string;
  changer?: {
    state: string;
  };
}

export interface JobResponse {
  id: string;
  status: string;
  state?: string;
  job_type?: string;
  type?: string;
  created_at: string;
  updated_at: string;
  error?: string | null;
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

export type SystemHealth = 'Healthy' | 'Degraded' | 'Critical';
