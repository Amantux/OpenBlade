import { apiRequest, rootApiRequest } from './client';

export interface LtfsVolume {
  barcode: string;
  label: string;
  mountPoint?: string;
  mounted: boolean;
  capacityGB: number;
  usedGB: number;
  fileCount?: number;
  lastModified?: string;
}

export interface LtfsFile {
  name: string;
  path: string;
  size: number;
  modified: string;
  type: 'file' | 'directory';
  tapeBarcode?: string;
  shardCount?: number;
}

export interface LtfsFileListResult {
  files: LtfsFile[];
  isSynthetic: boolean;
  note?: string;
}

interface LtfsCatalogEntry {
  path: string;
  size: number;
  tape_barcode: string;
  archived_at: string | null;
  shard_count: number;
}

interface LtfsSection {
  sectionNumber: number;
  name: string;
  status: string;
  mounted: boolean;
  mountPoint: string;
  fileSystem: string;
  partitionName: string;
  readOnly: boolean;
  lastMounted?: string | null;
}

interface LtfsSectionSummaryResponse {
  sectionList: {
    section: LtfsSection[];
  };
}

interface LtfsSectionMediaResponse {
  mediaList: {
    media: Array<{
      barcode: string;
      state: string;
      type: string;
    }>;
  };
}

interface LtfsSectionStatusResponse {
  status: {
    sectionNumber: number;
    state: string;
    mounted: boolean;
    health: string;
    activeMounts: number;
  };
}

interface MediaTypeCatalogResponse {
  typeList: {
    type: Array<{
      name: string;
      capacity: string;
    }>;
  };
}

interface VolumeCatalogSummary {
  fileCount: number;
  usedGB: number;
  lastModified?: string;
}

interface AmlVolumeMetadata {
  label: string;
  mountPoint?: string;
  mounted: boolean;
  capacityGB: number;
}

interface BrowseNode {
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified: string;
  tapeBarcode?: string;
  shardCount?: number;
}

function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed || trimmed === '/') {
    return '/';
  }
  return `/${trimmed.replace(/^\/+|\/+$/g, '')}`;
}

function parseCapacityGb(capacity: string | undefined, mediaType: string): number {
  const match = capacity?.match(/(\d+(?:\.\d+)?)\s*TB/i);
  if (match) {
    return Math.round(Number(match[1]) * 1024);
  }

  if (mediaType.toUpperCase().includes('LTO-9')) {
    return 18 * 1024;
  }

  return 12 * 1024;
}

function estimateUsedGb(barcode: string, mounts: number, capacityGB: number): number {
  const seed = barcode.split('').reduce((total, char) => total + char.charCodeAt(0), 0);
  const baseline = 0.22 + (seed % 45) / 100;
  const usageFromMounts = Math.min(mounts * 0.03, 0.2);
  return Math.min(Math.round(capacityGB * (baseline + usageFromMounts)), capacityGB);
}

function estimateFileCount(barcode: string, mounts: number): number {
  const seed = barcode.split('').reduce((total, char) => total + char.charCodeAt(0), 0);
  return 240 + (seed % 1400) + mounts * 17;
}

async function getSections() {
  const response = await apiRequest<LtfsSectionSummaryResponse>('/devices/blades/ltfs');
  return response.sectionList.section;
}

async function getSectionForBarcode(barcode: string) {
  const sections = await getSections();

  for (const section of sections) {
    const mediaResponse = await apiRequest<LtfsSectionMediaResponse>(`/devices/blade/ltfs/${section.sectionNumber}/media`);
    if (mediaResponse.mediaList.media.some((item) => item.barcode === barcode)) {
      return section;
    }
  }

  throw new Error(`LTFS volume ${barcode} was not found.`);
}

async function getCatalogTapeBarcodes(): Promise<string[]> {
  return rootApiRequest<string[]>('/ltfs/tapes');
}

async function getCatalogEntries(barcode: string, pathPrefix = '/'): Promise<LtfsCatalogEntry[]> {
  const params = new URLSearchParams({
    tape_barcode: barcode,
    path_prefix: normalizePath(pathPrefix),
  });
  return rootApiRequest<LtfsCatalogEntry[]>(`/ltfs/browse?${params.toString()}`);
}

async function getVolumeCatalogSummary(barcode: string): Promise<VolumeCatalogSummary> {
  const entries = await getCatalogEntries(barcode, '/');
  const totalBytes = entries.reduce((sum, entry) => sum + entry.size, 0);
  const lastModified = entries
    .map((entry) => entry.archived_at ?? undefined)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1);

  return {
    fileCount: entries.length,
    usedGB: Number((totalBytes / 1024 ** 3).toFixed(2)),
    lastModified,
  };
}

async function getAmlVolumeMetadata(): Promise<Map<string, AmlVolumeMetadata>> {
  const [sections, typeCatalog] = await Promise.all([
    getSections(),
    apiRequest<MediaTypeCatalogResponse>('/media/types'),
  ]);

  const capacityByType = new Map(typeCatalog.typeList.type.map((item) => [item.name, parseCapacityGb(item.capacity, item.name)]));
  const sectionDetails = await Promise.all(
    sections.map(async (section) => {
      const [mediaResponse, statusResponse] = await Promise.all([
        apiRequest<LtfsSectionMediaResponse>(`/devices/blade/ltfs/${section.sectionNumber}/media`),
        apiRequest<LtfsSectionStatusResponse>(`/devices/blade/ltfs/${section.sectionNumber}/status`),
      ]);
      return { section, media: mediaResponse.mediaList.media, status: statusResponse.status };
    }),
  );

  const volumes = new Map<string, AmlVolumeMetadata>();
  for (const { section, media, status } of sectionDetails) {
    for (const tape of media) {
      volumes.set(tape.barcode, {
        label: section.name,
        mountPoint: status.mounted ? section.mountPoint : undefined,
        mounted: status.mounted,
        capacityGB: capacityByType.get(tape.type) ?? parseCapacityGb(undefined, tape.type),
      });
    }
  }

  return volumes;
}

async function getFallbackVolumes(): Promise<LtfsVolume[]> {
  const amlVolumes = await getAmlVolumeMetadata();
  return Array.from(amlVolumes.entries())
    .map(([barcode, volume]) => ({
      barcode,
      label: `${volume.label} (AML inventory fallback)`,
      mountPoint: volume.mountPoint,
      mounted: volume.mounted,
      capacityGB: volume.capacityGB,
      usedGB: estimateUsedGb(barcode, 0, volume.capacityGB),
      fileCount: estimateFileCount(barcode, 0),
    }))
    .sort((left, right) => left.barcode.localeCompare(right.barcode));
}

export async function getLtfsVolumes(): Promise<LtfsVolume[]> {
  try {
    const barcodes = await getCatalogTapeBarcodes();
    if (barcodes.length === 0) {
      return [];
    }

    const [catalogSummaries, amlVolumes] = await Promise.all([
      Promise.all(barcodes.map(async (barcode) => [barcode, await getVolumeCatalogSummary(barcode)] as const)),
      getAmlVolumeMetadata().catch(() => new Map<string, AmlVolumeMetadata>()),
    ]);

    return catalogSummaries
      .map(([barcode, summary]) => {
        const amlVolume = amlVolumes.get(barcode);
        return {
          barcode,
          label: amlVolume?.label ?? 'Catalog metadata',
          mountPoint: amlVolume?.mountPoint,
          mounted: amlVolume?.mounted ?? false,
          capacityGB: amlVolume?.capacityGB ?? 12 * 1024,
          usedGB: summary.usedGB,
          fileCount: summary.fileCount,
          lastModified: summary.lastModified,
        } satisfies LtfsVolume;
      })
      .sort((left, right) => left.barcode.localeCompare(right.barcode));
  } catch {
    return getFallbackVolumes();
  }
}

export async function mountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/mount`, { method: 'POST' });
}

export async function unmountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/unmount`, { method: 'POST' });
}

function simulateTree(barcode: string): BrowseNode[] {
  const prefix = barcode.toLowerCase();

  return [
    { path: '/backups', type: 'directory', modified: '2024-01-15T03:18:00Z' },
    { path: '/backups/2024', type: 'directory', modified: '2024-01-15T03:18:00Z' },
    { path: '/backups/2024/january', type: 'directory', modified: '2024-01-14T22:10:00Z' },
    { path: `/backups/2024/january/${prefix}-cluster-full-2024-01-14.tar`, type: 'file', size: 2_947_483_648, modified: '2024-01-14T22:10:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: `/backups/2024/january/${prefix}-catalog-delta-2024-01-15.tar.gz`, type: 'file', size: 438_845_440, modified: '2024-01-15T03:18:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: '/backups/2023', type: 'directory', modified: '2023-12-28T10:40:00Z' },
    { path: `/backups/2023/${prefix}-year-end-validation-report.pdf`, type: 'file', size: 16_488_920, modified: '2023-12-28T10:40:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: '/exports', type: 'directory', modified: '2024-01-12T12:00:00Z' },
    { path: `/exports/${prefix}-restore-index.csv`, type: 'file', size: 1_248_400, modified: '2024-01-12T12:00:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: '/logs', type: 'directory', modified: '2024-01-15T06:05:00Z' },
    { path: `/logs/${prefix}-ltfs-mount.log`, type: 'file', size: 284_112, modified: '2024-01-15T06:05:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: `/logs/${prefix}-integrity-scan.log`, type: 'file', size: 832_516, modified: '2024-01-11T19:44:00Z', tapeBarcode: barcode, shardCount: 1 },
    { path: '/manifests', type: 'directory', modified: '2024-01-10T08:30:00Z' },
    { path: `/manifests/${prefix}-tape-manifest.json`, type: 'file', size: 94_320, modified: '2024-01-10T08:30:00Z', tapeBarcode: barcode, shardCount: 1 },
  ];
}

function buildDirectoryListing(nodes: BrowseNode[], path = '/'): LtfsFile[] {
  const normalizedPath = normalizePath(path);
  const baseParts = normalizedPath === '/' ? [] : normalizedPath.split('/').filter(Boolean);
  const childMap = new Map<string, LtfsFile>();

  for (const node of nodes) {
    const normalizedNodePath = normalizePath(node.path);
    const nodeParts = normalizedNodePath.split('/').filter(Boolean);
    if (!baseParts.every((part, index) => nodeParts[index] === part)) {
      continue;
    }

    const remainder = nodeParts.slice(baseParts.length);
    if (remainder.length === 0) {
      continue;
    }

    const childName = remainder[0];
    const childPath = `/${[...baseParts, childName].join('/')}`;
    if (remainder.length === 1 && node.type === 'file') {
      childMap.set(childPath, {
        name: childName,
        path: childPath,
        size: node.size ?? 0,
        modified: node.modified,
        type: 'file',
        tapeBarcode: node.tapeBarcode,
        shardCount: node.shardCount,
      });
      continue;
    }

    const current = childMap.get(childPath);
    const modified = current && current.modified > node.modified ? current.modified : node.modified;
    childMap.set(childPath, {
      name: childName,
      path: childPath,
      size: 0,
      modified,
      type: 'directory',
    });
  }

  return Array.from(childMap.values()).sort((left, right) => {
    if (left.type !== right.type) {
      return left.type === 'directory' ? -1 : 1;
    }
    return left.name.localeCompare(right.name);
  });
}

function toCatalogNodes(entries: LtfsCatalogEntry[]): BrowseNode[] {
  return entries.map((entry) => ({
    path: entry.path,
    type: 'file',
    size: entry.size,
    modified: entry.archived_at ?? '1970-01-01T00:00:00Z',
    tapeBarcode: entry.tape_barcode,
    shardCount: entry.shard_count,
  }));
}

function syntheticFallback(barcode: string, path: string, note: string): LtfsFileListResult {
  return {
    files: buildDirectoryListing(simulateTree(barcode), path),
    isSynthetic: true,
    note,
  };
}

export async function listFiles(barcode: string, path = '/'): Promise<LtfsFileListResult> {
  try {
    const entries = await getCatalogEntries(barcode, path);
    if (entries.length === 0) {
      return syntheticFallback(
        barcode,
        path,
        'Synthetic fallback: backend catalog returned no browse entries for this selection.',
      );
    }

    return {
      files: buildDirectoryListing(toCatalogNodes(entries), path),
      isSynthetic: false,
    };
  } catch {
    return syntheticFallback(
      barcode,
      path,
      'Synthetic fallback: backend catalog browse is unavailable right now.',
    );
  }
}
