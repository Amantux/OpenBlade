import { apiRequest } from '../api/client';

export type PoolPolicy = 'critical' | 'standard' | 'archive' | 'scratch' | 'cleaning';

export interface MediaPool {
  id: string;
  name: string;
  policy: PoolPolicy;
  maxDrives: number;
  targetLtoGeneration: string | null;
  quotaGB: number | null;
  assignedBarcodes: string[];
  color: string;
  createdAt: string;
  mediaCount?: number;
}

interface AmlMediaPoolRecord {
  id: string;
  name: string;
  policy?: string | null;
  maxDrives?: number | null;
  targetLtoGeneration?: string | null;
  quotaGB?: number | null;
  assignedBarcodes?: string[] | null;
  color?: string | null;
  createdAt?: string | null;
  mediaCount?: number;
}

interface AmlMediaPoolListResponse {
  poolList: {
    pool: AmlMediaPoolRecord[];
  };
}

interface AmlMediaPoolResponse {
  pool: AmlMediaPoolRecord;
}

export const MEDIA_POOL_STORE_EVENT = 'openblade:media-pools-changed';
export const MEDIA_POOL_COLOR_PRESETS = ['#DC2626', '#2563EB', '#059669', '#7C3AED', '#EA580C'] as const;
export const MEDIA_POOL_GENERATIONS = ['LTO-7', 'LTO-8', 'LTO-9'] as const;

function dispatchPoolChange(): void {
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(MEDIA_POOL_STORE_EVENT));
  }
}

function normalizePolicy(value: unknown): PoolPolicy {
  switch (value) {
    case 'critical':
    case 'standard':
    case 'archive':
    case 'scratch':
    case 'cleaning':
      return value;
    default:
      return 'standard';
  }
}

function normalizeMaxDrives(value: unknown): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    return 4;
  }

  return Math.min(8, Math.max(1, Math.round(parsed)));
}

function normalizeGeneration(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function normalizeQuota(value: unknown): number | null {
  if (value === null || value === undefined || value === '') {
    return null;
  }

  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return null;
  }

  return Math.round(parsed);
}

function normalizeColor(value: unknown, fallback: string): string {
  return typeof value === 'string' && /^#[0-9A-Fa-f]{6}$/.test(value) ? value.toUpperCase() : fallback;
}

function normalizeAssignedBarcodes(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return Array.from(
    new Set(
      value
        .filter((item): item is string => typeof item === 'string')
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function mapPool(value: AmlMediaPoolRecord, index: number): MediaPool {
  const fallbackColor = MEDIA_POOL_COLOR_PRESETS[index % MEDIA_POOL_COLOR_PRESETS.length];
  return {
    id: value.id,
    name: value.name.trim(),
    policy: normalizePolicy(value.policy),
    maxDrives: normalizeMaxDrives(value.maxDrives),
    targetLtoGeneration: normalizeGeneration(value.targetLtoGeneration),
    quotaGB: normalizeQuota(value.quotaGB),
    assignedBarcodes: normalizeAssignedBarcodes(value.assignedBarcodes),
    color: normalizeColor(value.color, fallbackColor),
    createdAt: typeof value.createdAt === 'string' && value.createdAt ? value.createdAt : new Date().toISOString(),
    mediaCount: typeof value.mediaCount === 'number' ? value.mediaCount : undefined,
  };
}

function poolPayload(pool: Pick<MediaPool, 'name' | 'policy' | 'maxDrives' | 'targetLtoGeneration' | 'quotaGB' | 'color'>) {
  return {
    name: pool.name.trim(),
    policy: normalizePolicy(pool.policy),
    maxDrives: normalizeMaxDrives(pool.maxDrives),
    targetLtoGeneration: normalizeGeneration(pool.targetLtoGeneration),
    quotaGB: normalizeQuota(pool.quotaGB),
    color: normalizeColor(pool.color, MEDIA_POOL_COLOR_PRESETS[0]),
  };
}

export async function listPools(): Promise<MediaPool[]> {
  const response = await apiRequest<AmlMediaPoolListResponse>('/media/pools');
  return response.poolList.pool.map(mapPool);
}

export async function getPool(id: string): Promise<MediaPool | null> {
  if (!id.trim()) {
    return null;
  }

  const response = await apiRequest<AmlMediaPoolResponse>(`/media/pools/${encodeURIComponent(id)}`);
  return mapPool(response.pool, 0);
}

export async function savePool(
  pool: Partial<MediaPool> & Pick<MediaPool, 'name' | 'policy' | 'maxDrives' | 'targetLtoGeneration' | 'quotaGB' | 'color'>,
): Promise<MediaPool> {
  const response = pool.id
    ? await apiRequest<AmlMediaPoolResponse>(`/media/pools/${encodeURIComponent(pool.id)}`, {
        method: 'PUT',
        body: poolPayload(pool),
      })
    : await apiRequest<AmlMediaPoolResponse>('/media/pools', {
        method: 'POST',
        body: poolPayload(pool),
      });

  dispatchPoolChange();
  return mapPool(response.pool, 0);
}

export async function deletePool(poolId: string): Promise<void> {
  if (!poolId.trim()) {
    return;
  }

  await apiRequest(`/media/pools/${encodeURIComponent(poolId)}`, { method: 'DELETE' });
  dispatchPoolChange();
}

export async function assignBarcode(poolId: string, barcode: string): Promise<void> {
  const normalizedPoolId = poolId.trim();
  const normalizedBarcode = barcode.trim();
  if (!normalizedPoolId || !normalizedBarcode) {
    return;
  }

  await apiRequest(`/media/pools/${encodeURIComponent(normalizedPoolId)}/assign`, {
    method: 'POST',
    body: { barcodes: [normalizedBarcode] },
  });
  dispatchPoolChange();
}

export async function unassignBarcode(poolId: string, barcode: string): Promise<void> {
  const normalizedPoolId = poolId.trim();
  const normalizedBarcode = barcode.trim();
  if (!normalizedPoolId || !normalizedBarcode) {
    return;
  }

  await apiRequest(`/media/pools/${encodeURIComponent(normalizedPoolId)}/assign/${encodeURIComponent(normalizedBarcode)}`, {
    method: 'DELETE',
  });
  dispatchPoolChange();
}

export const getPools = listPools;

export async function createPool(pool: Omit<MediaPool, 'id' | 'createdAt'>): Promise<MediaPool> {
  return savePool(pool);
}

export async function updatePool(id: string, patch: Partial<MediaPool>): Promise<void> {
  const currentPool = await getPool(id);
  if (!currentPool) {
    return;
  }

  await savePool({
    ...currentPool,
    ...patch,
    id,
    name: typeof patch.name === 'string' ? patch.name : currentPool.name,
    policy: patch.policy !== undefined ? patch.policy : currentPool.policy,
    maxDrives: patch.maxDrives !== undefined ? patch.maxDrives : currentPool.maxDrives,
    targetLtoGeneration:
      patch.targetLtoGeneration !== undefined ? patch.targetLtoGeneration : currentPool.targetLtoGeneration,
    quotaGB: patch.quotaGB !== undefined ? patch.quotaGB : currentPool.quotaGB,
    color: patch.color !== undefined ? patch.color : currentPool.color,
  });
}

export const assignCartridge = assignBarcode;
export const unassignCartridge = unassignBarcode;
