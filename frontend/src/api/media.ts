import { apiRequest } from './client';

export interface Cartridge {
  barcode: string;
  type: string;
  partition?: string;
  slotAddress?: string;
  state: string;
  writeProtected?: boolean;
  worm?: boolean;
  generations?: number;
  loadCount?: number;
  errorCount?: number;
  lastLoaded?: string;
  capacityGB?: number;
  usedGB?: number;
  percentUsed?: number;
  poolName?: string;
}

interface AmlMediaRecord {
  barcode: string;
  type: string;
  partition?: string | null;
  slotAddress?: string;
  state: string;
  writeProtected?: boolean;
  worm?: boolean;
  generations?: number;
  loadCount?: number;
  errorCount?: number;
  lastLoaded?: string | null;
  capacityGB?: number;
  usedGB?: number;
  percentUsed?: number;
  poolName?: string | null;
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
  'LTO-6': 2500,
  'LTO-7': 6000,
  'LTO-8': 12000,
  'LTO-9': 18000,
};

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value)));
}

function inferCapacityGB(type: string): number {
  const matched = Object.keys(CAPACITY_BY_TYPE).find((candidate) => type.startsWith(candidate));
  return matched ? CAPACITY_BY_TYPE[matched] : 6000;
}

function mapCartridge(media: AmlMediaRecord): Cartridge {
  const capacityGB = typeof media.capacityGB === 'number' && Number.isFinite(media.capacityGB)
    ? media.capacityGB
    : inferCapacityGB(media.type);
  const usedGB = typeof media.usedGB === 'number' && Number.isFinite(media.usedGB)
    ? media.usedGB
    : 0;
  const percentUsed = typeof media.percentUsed === 'number' && Number.isFinite(media.percentUsed)
    ? clampPercent(media.percentUsed)
    : capacityGB > 0
      ? clampPercent((usedGB / capacityGB) * 100)
      : 0;

  return {
    barcode: media.barcode,
    type: media.type,
    partition: media.partition ?? undefined,
    slotAddress: media.slotAddress,
    state: media.state,
    writeProtected: media.writeProtected,
    worm: media.worm,
    generations: media.generations,
    loadCount: media.loadCount,
    errorCount: media.errorCount,
    lastLoaded: media.lastLoaded ?? undefined,
    capacityGB,
    usedGB,
    percentUsed,
    poolName: media.poolName ?? undefined,
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
