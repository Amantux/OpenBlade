import { apiRequest, rootApiRequest } from './client';
import { listCartridges } from './media';

export interface LtfsVolume {
  barcode: string;
  label: string;
  mountPoint?: string;
  mounted: boolean;
  capacityGB: number;
  usedGB: number;
  fileCount?: number;
  lastModified?: string;
  shardCount?: number;
  hasCatalog?: boolean;
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
  shardCount: number;
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
    shardCount: entries.reduce((sum, entry) => sum + Math.max(entry.shard_count, 1), 0),
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

export async function getLtfsVolumes(): Promise<LtfsVolume[]> {
  const [catalogBarcodes, amlVolumes, cartridges] = await Promise.all([
    getCatalogTapeBarcodes().catch(() => [] as string[]),
    getAmlVolumeMetadata().catch(() => new Map<string, AmlVolumeMetadata>()),
    listCartridges().catch(() => []),
  ]);

  const ltfsCartridgeBarcodes = cartridges
    .filter((cartridge) => String(cartridge.type ?? '').toUpperCase().startsWith('LTO-'))
    .map((cartridge) => cartridge.barcode);
  const barcodes = Array.from(new Set([...catalogBarcodes, ...amlVolumes.keys(), ...ltfsCartridgeBarcodes]))
    .sort((left, right) => left.localeCompare(right));

  if (barcodes.length === 0) {
    return [];
  }

  const catalogSummaries = new Map(
    await Promise.all(
      barcodes.map(async (barcode) => {
        try {
          return [barcode, await getVolumeCatalogSummary(barcode)] as const;
        } catch {
          return [barcode, { fileCount: 0, usedGB: 0, shardCount: 0 }] as const;
        }
      }),
    ),
  );

  return barcodes
    .map((barcode) => {
      const amlVolume = amlVolumes.get(barcode);
      const summary = catalogSummaries.get(barcode);
      return {
        barcode,
        label: amlVolume?.label ?? barcode,
        mountPoint: amlVolume?.mountPoint,
        mounted: amlVolume?.mounted ?? false,
        capacityGB: amlVolume?.capacityGB ?? 0,
        usedGB: summary?.usedGB ?? 0,
        fileCount: summary?.fileCount ?? 0,
        lastModified: summary?.lastModified,
        shardCount: summary?.shardCount ?? 0,
        hasCatalog: Boolean(summary?.fileCount),
      } satisfies LtfsVolume;
    })
    .sort((left, right) => left.barcode.localeCompare(right.barcode));
}

export async function mountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/mount`, { method: 'POST' });
}

export async function unmountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/unmount`, { method: 'POST' });
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

export async function listFiles(barcode: string, path = '/'): Promise<LtfsFileListResult> {
  const entries = await getCatalogEntries(barcode, path);
  return {
    files: buildDirectoryListing(toCatalogNodes(entries), path),
  };
}
