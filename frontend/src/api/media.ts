import { apiRequest } from './client';

export interface Cartridge {
  barcode: string;
  type: string;
  state: string;
  location: string;
  pool?: string;
  capacityGB?: number;
  usedGB?: number;
  percentUsed?: number;
  partition?: string | null;
  slotAddress?: string;
  writeProtected?: boolean;
  worm?: boolean;
  generations?: number;
  loadCount?: number;
  errorCount?: number;
  lastLoaded?: string | null;
}

interface AmlMediaRecord {
  barcode: string;
  type: string;
  partition?: string | null;
  slotAddress?: string;
  location?: string;
  state: string;
  writeProtected?: boolean;
  worm?: boolean;
  generations?: number;
  loadCount?: number;
  errorCount?: number;
  lastLoaded?: string | null;
  pool?: string;
  capacityGB?: number;
  usedGB?: number;
  percentUsed?: number;
}

interface AmlMediaListResponse {
  mediaList: {
    media: AmlMediaRecord[];
  };
}

interface AmlMediaResponse {
  media: AmlMediaRecord;
}

const CAPACITY_BY_TYPE: Record<string, number> = {
  'LTO-7': 6000,
  'LTO-8': 12000,
  'LTO-9': 18000,
};

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value)));
}

function inferCapacityGB(type: string): number {
  const matched = Object.keys(CAPACITY_BY_TYPE).find((candidate) => type.startsWith(candidate));
  return matched ? CAPACITY_BY_TYPE[matched] : 0;
}

function inferPercentUsed(media: AmlMediaRecord, capacityGB: number): number {
  if (typeof media.percentUsed === 'number' && Number.isFinite(media.percentUsed)) {
    return clampPercent(media.percentUsed);
  }

  if (typeof media.usedGB === 'number' && Number.isFinite(media.usedGB) && capacityGB > 0) {
    return clampPercent((media.usedGB / capacityGB) * 100);
  }

  if (capacityGB <= 0) {
    return 0;
  }

  const loadCount = typeof media.loadCount === 'number' ? media.loadCount : 0;
  const errorCount = typeof media.errorCount === 'number' ? media.errorCount : 0;
  const state = String(media.state ?? '').toLowerCase();
  let estimate = 18 + Math.min(loadCount * 4, 54) + Math.min(errorCount * 6, 12);

  if (media.worm) {
    estimate += 20;
  }
  if (state.includes('mounted') || state.includes('loaded')) {
    estimate += 10;
  }
  if (state.includes('empty') || state.includes('available')) {
    estimate -= 10;
  }

  return clampPercent(estimate);
}

function mapCartridge(media: AmlMediaRecord): Cartridge {
  const capacityGB = typeof media.capacityGB === 'number' && Number.isFinite(media.capacityGB)
    ? media.capacityGB
    : inferCapacityGB(media.type);
  const percentUsed = inferPercentUsed(media, capacityGB);
  const usedGB = typeof media.usedGB === 'number' && Number.isFinite(media.usedGB)
    ? media.usedGB
    : capacityGB > 0
      ? Math.round((capacityGB * percentUsed) / 100)
      : 0;

  return {
    barcode: media.barcode,
    type: media.type,
    state: media.state,
    location: media.location ?? media.slotAddress ?? 'Unknown',
    pool: media.pool,
    capacityGB,
    usedGB,
    percentUsed,
    partition: media.partition ?? null,
    slotAddress: media.slotAddress,
    writeProtected: media.writeProtected,
    worm: media.worm,
    generations: media.generations,
    loadCount: media.loadCount,
    errorCount: media.errorCount,
    lastLoaded: media.lastLoaded ?? null,
  };
}

export async function listCartridges(): Promise<Cartridge[]> {
  const response = await apiRequest<AmlMediaListResponse>('/media');
  return response.mediaList.media.map(mapCartridge);
}

export async function getCartridge(barcode: string): Promise<Cartridge> {
  const response = await apiRequest<AmlMediaResponse>(`/media/${encodeURIComponent(barcode)}`);
  return mapCartridge(response.media);
}
