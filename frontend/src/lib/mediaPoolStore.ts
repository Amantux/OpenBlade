import { generateId } from './utils';

import { ApiError, apiRequest } from '../api/client';

export type PoolPolicy = 'critical' | 'standard' | 'archive';

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
}

export const MEDIA_POOL_STORE_EVENT = 'openblade:media-pools-changed';
export const MEDIA_POOL_COLOR_PRESETS = ['#DC2626', '#2563EB', '#059669', '#7C3AED', '#EA580C'] as const;
export const MEDIA_POOL_GENERATIONS = ['LTO-7', 'LTO-8', 'LTO-9'] as const;

const STORAGE_KEY = 'openblade.media-pools.entries';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function normalizePolicy(value: unknown): PoolPolicy {
  switch (value) {
    case 'critical':
    case 'standard':
    case 'archive':
      return value;
    default:
      return 'standard';
  }
}

function normalizeMaxDrives(value: unknown, policy: PoolPolicy): number {
  if (policy === 'critical') {
    return 1;
  }

  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) {
    return 4;
  }

  return Math.min(8, Math.max(1, Math.round(parsed)));
}

function normalizeGeneration(value: unknown): string | null {
  return typeof value === 'string' && MEDIA_POOL_GENERATIONS.includes(value as (typeof MEDIA_POOL_GENERATIONS)[number]) ? value : null;
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

function defaultPools(): MediaPool[] {
  const createdAt = new Date().toISOString();
  return [
    {
      id: generateId(),
      name: 'Critical Backups',
      policy: 'critical',
      maxDrives: 1,
      targetLtoGeneration: 'LTO-8',
      quotaGB: 10000,
      assignedBarcodes: [],
      color: '#DC2626',
      createdAt,
    },
    {
      id: generateId(),
      name: 'General Archive',
      policy: 'standard',
      maxDrives: 4,
      targetLtoGeneration: null,
      quotaGB: null,
      assignedBarcodes: [],
      color: '#2563EB',
      createdAt,
    },
    {
      id: generateId(),
      name: 'Cold Storage',
      policy: 'archive',
      maxDrives: 2,
      targetLtoGeneration: 'LTO-9',
      quotaGB: null,
      assignedBarcodes: [],
      color: '#7C3AED',
      createdAt,
    },
  ];
}

function persistPools(entries: MediaPool[]): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(entries));
  window.dispatchEvent(new Event(MEDIA_POOL_STORE_EVENT));
}

function normalizePool(value: unknown, index: number): MediaPool | null {
  if (!isRecord(value)) {
    return null;
  }

  const policy = normalizePolicy(value.policy);
  const fallbackColor = MEDIA_POOL_COLOR_PRESETS[index % MEDIA_POOL_COLOR_PRESETS.length];
  const name = typeof value.name === 'string' && value.name.trim() ? value.name.trim() : '';

  if (!name) {
    return null;
  }

  return {
    id: typeof value.id === 'string' && value.id ? value.id : generateId(),
    name,
    policy,
    maxDrives: normalizeMaxDrives(value.maxDrives, policy),
    targetLtoGeneration: normalizeGeneration(value.targetLtoGeneration),
    quotaGB: normalizeQuota(value.quotaGB),
    assignedBarcodes: normalizeAssignedBarcodes(value.assignedBarcodes),
    color: normalizeColor(value.color, fallbackColor),
    createdAt: typeof value.createdAt === 'string' && value.createdAt ? value.createdAt : new Date().toISOString(),
  };
}

function readStoredPools(): MediaPool[] {
  if (typeof window === 'undefined') {
    return defaultPools();
  }

  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (!raw) {
    const seeded = defaultPools();
    persistPools(seeded);
    return seeded;
  }

  try {
    const parsed = JSON.parse(raw) as unknown;
    const pools = Array.isArray(parsed)
      ? parsed
          .map((item, index) => normalizePool(item, index))
          .filter((item): item is MediaPool => item !== null)
      : [];

    if (pools.length === 0) {
      const seeded = defaultPools();
      persistPools(seeded);
      return seeded;
    }

    return pools;
  } catch {
    const seeded = defaultPools();
    persistPools(seeded);
    return seeded;
  }
}

function encodePoolName(name: string): string {
  return encodeURIComponent(name);
}

function backendPoolPayload(pool: MediaPool): { pool: { type: string; policy: PoolPolicy } } {
  return {
    pool: {
      type: pool.targetLtoGeneration ?? 'LTO-9',
      policy: pool.policy,
    },
  };
}

async function upsertBackendPool(pool: MediaPool): Promise<void> {
  const path = `/media/pool/${encodePoolName(pool.name)}`;
  try {
    await apiRequest(path, { method: 'PUT', body: backendPoolPayload(pool) });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      throw error;
    }
    await apiRequest(path, { method: 'POST', body: backendPoolPayload(pool) });
  }
}

async function deleteBackendPool(name: string): Promise<void> {
  try {
    await apiRequest(`/media/pool/${encodePoolName(name)}`, { method: 'DELETE' });
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 404) {
      throw error;
    }
  }
}

function persistAssignmentCache(poolId: string, barcode: string): void {
  const normalizedBarcode = barcode.trim();
  const next = readStoredPools().map((pool) => {
    const assigned = pool.assignedBarcodes.filter((item) => item !== normalizedBarcode);
    if (pool.id !== poolId) {
      return { ...pool, assignedBarcodes: assigned };
    }
    return { ...pool, assignedBarcodes: [...assigned, normalizedBarcode] };
  });
  persistPools(next);
}

function removeFromAssignmentCache(poolId: string, barcode: string): void {
  const normalizedBarcode = barcode.trim();
  const next = readStoredPools().map((pool) => (
    pool.id === poolId
      ? { ...pool, assignedBarcodes: pool.assignedBarcodes.filter((item) => item !== normalizedBarcode) }
      : pool
  ));
  persistPools(next);
}

export function getPools(): MediaPool[] {
  return readStoredPools();
}

export async function createPool(pool: Omit<MediaPool, 'id' | 'createdAt'>): Promise<MediaPool> {
  const policy = normalizePolicy(pool.policy);
  const created: MediaPool = {
    id: generateId(),
    name: pool.name.trim(),
    policy,
    maxDrives: normalizeMaxDrives(pool.maxDrives, policy),
    targetLtoGeneration: normalizeGeneration(pool.targetLtoGeneration),
    quotaGB: normalizeQuota(pool.quotaGB),
    assignedBarcodes: normalizeAssignedBarcodes(pool.assignedBarcodes),
    color: normalizeColor(pool.color, MEDIA_POOL_COLOR_PRESETS[0]),
    createdAt: new Date().toISOString(),
  };

  await upsertBackendPool(created);
  persistPools([...readStoredPools(), created]);
  return created;
}

export async function updatePool(id: string, patch: Partial<MediaPool>): Promise<void> {
  const pools = readStoredPools();
  const currentPool = pools.find((pool) => pool.id === id);
  if (!currentPool) {
    return;
  }

  const next = pools.map((pool, index) => {
    if (pool.id !== id) {
      return pool;
    }

    const policy = patch.policy === undefined ? pool.policy : normalizePolicy(patch.policy);
    return {
      ...pool,
      ...patch,
      id: pool.id,
      createdAt: pool.createdAt,
      name: typeof patch.name === 'string' && patch.name.trim() ? patch.name.trim() : pool.name,
      policy,
      maxDrives: normalizeMaxDrives(patch.maxDrives ?? pool.maxDrives, policy),
      targetLtoGeneration: patch.targetLtoGeneration === undefined ? pool.targetLtoGeneration : normalizeGeneration(patch.targetLtoGeneration),
      quotaGB: patch.quotaGB === undefined ? pool.quotaGB : normalizeQuota(patch.quotaGB),
      assignedBarcodes: patch.assignedBarcodes === undefined ? pool.assignedBarcodes : normalizeAssignedBarcodes(patch.assignedBarcodes),
      color: patch.color === undefined ? pool.color : normalizeColor(patch.color, MEDIA_POOL_COLOR_PRESETS[index % MEDIA_POOL_COLOR_PRESETS.length]),
    };
  });

  const updatedPool = next.find((pool) => pool.id === id);
  if (!updatedPool) {
    return;
  }

  await upsertBackendPool(updatedPool);
  if (currentPool.name !== updatedPool.name && updatedPool.assignedBarcodes.length > 0) {
    await apiRequest(`/media/pool/${encodePoolName(updatedPool.name)}/assign`, {
      method: 'POST',
      body: { barcodeList: { barcode: updatedPool.assignedBarcodes } },
    });
    await deleteBackendPool(currentPool.name);
  } else if (currentPool.name !== updatedPool.name) {
    await deleteBackendPool(currentPool.name);
  }

  persistPools(next);
}

export async function deletePool(id: string): Promise<void> {
  const pools = readStoredPools();
  const pool = pools.find((entry) => entry.id === id);
  if (!pool) {
    return;
  }

  await deleteBackendPool(pool.name);
  persistPools(pools.filter((entry) => entry.id !== id));
}

export async function assignCartridge(poolId: string, barcode: string): Promise<void> {
  const normalizedBarcode = barcode.trim();
  if (!normalizedBarcode) {
    return;
  }

  const pool = readStoredPools().find((entry) => entry.id === poolId);
  if (!pool) {
    return;
  }

  await upsertBackendPool(pool);
  await apiRequest(`/media/pool/${encodePoolName(pool.name)}/assign`, {
    method: 'POST',
    body: { barcodeList: { barcode: [normalizedBarcode] } },
  });
  persistAssignmentCache(poolId, normalizedBarcode);
}

export async function unassignCartridge(poolId: string, barcode: string): Promise<void> {
  const normalizedBarcode = barcode.trim();
  if (!normalizedBarcode) {
    return;
  }

  const pool = readStoredPools().find((entry) => entry.id === poolId);
  if (!pool) {
    return;
  }

  await apiRequest(`/media/pool/${encodePoolName(pool.name)}/unassign`, {
    method: 'POST',
    body: { barcodeList: { barcode: [normalizedBarcode] } },
  });
  removeFromAssignmentCache(poolId, normalizedBarcode);
}
