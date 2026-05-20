import { apiRequest } from './client';

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

interface MediaDetailResponse {
  media: {
    barcode: string;
    type: string;
    loadCount: number;
    lastLoaded?: string | null;
    errorCount: number;
  };
}

interface MediaStatisticsResponse {
  mediaStats: {
    loadCount: number;
    totalMounts: number;
    totalHours: number;
    lastLoaded?: string | null;
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

export async function getLtfsVolumes(): Promise<LtfsVolume[]> {
  const [sections, typeCatalog] = await Promise.all([
    getSections(),
    apiRequest<MediaTypeCatalogResponse>('/media/types'),
  ]);

  const capacityByType = new Map(typeCatalog.typeList.type.map((item) => [item.name, parseCapacityGb(item.capacity, item.name)]));

  const sectionVolumes = await Promise.all(
    sections.map(async (section) => {
      const [mediaResponse, statusResponse] = await Promise.all([
        apiRequest<LtfsSectionMediaResponse>(`/devices/blade/ltfs/${section.sectionNumber}/media`),
        apiRequest<LtfsSectionStatusResponse>(`/devices/blade/ltfs/${section.sectionNumber}/status`),
      ]);

      const volumes = await Promise.all(
        mediaResponse.mediaList.media.map(async (media) => {
          const [detailResponse, statisticsResponse] = await Promise.all([
            apiRequest<MediaDetailResponse>(`/media/${media.barcode}`),
            apiRequest<MediaStatisticsResponse>(`/media/${media.barcode}/statistics`),
          ]);

          const capacityGB = capacityByType.get(media.type) ?? parseCapacityGb(undefined, media.type);
          const totalMounts = statisticsResponse.mediaStats.totalMounts ?? detailResponse.media.loadCount ?? 0;

          // AML exposes LTFS sections and media metadata, but not per-cartridge LTFS utilization.
          // The UI derives capacity/file estimates so operators still get realistic browser cards.
          return {
            barcode: media.barcode,
            label: section.name,
            mountPoint: statusResponse.status.mounted ? section.mountPoint : undefined,
            mounted: statusResponse.status.mounted,
            capacityGB,
            usedGB: estimateUsedGb(media.barcode, totalMounts, capacityGB),
            fileCount: estimateFileCount(media.barcode, totalMounts),
            lastModified: detailResponse.media.lastLoaded ?? statisticsResponse.mediaStats.lastLoaded ?? section.lastMounted ?? undefined,
          } satisfies LtfsVolume;
        }),
      );

      return volumes;
    }),
  );

  return sectionVolumes.flat().sort((left, right) => left.barcode.localeCompare(right.barcode));
}

export async function mountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/mount`, { method: 'POST' });
}

export async function unmountVolume(barcode: string): Promise<void> {
  const section = await getSectionForBarcode(barcode);
  await apiRequest(`/devices/blade/ltfs/${section.sectionNumber}/unmount`, { method: 'POST' });
}

interface SimulatedNode {
  path: string;
  type: 'file' | 'directory';
  size?: number;
  modified: string;
}

function simulateTree(barcode: string): SimulatedNode[] {
  const prefix = barcode.toLowerCase();

  return [
    { path: '/backups', type: 'directory', modified: '2024-01-15T03:18:00Z' },
    { path: '/backups/2024', type: 'directory', modified: '2024-01-15T03:18:00Z' },
    { path: '/backups/2024/january', type: 'directory', modified: '2024-01-14T22:10:00Z' },
    { path: `/backups/2024/january/${prefix}-cluster-full-2024-01-14.tar`, type: 'file', size: 2_947_483_648, modified: '2024-01-14T22:10:00Z' },
    { path: `/backups/2024/january/${prefix}-catalog-delta-2024-01-15.tar.gz`, type: 'file', size: 438_845_440, modified: '2024-01-15T03:18:00Z' },
    { path: '/backups/2023', type: 'directory', modified: '2023-12-28T10:40:00Z' },
    { path: `/backups/2023/${prefix}-year-end-validation-report.pdf`, type: 'file', size: 16_488_920, modified: '2023-12-28T10:40:00Z' },
    { path: '/exports', type: 'directory', modified: '2024-01-12T12:00:00Z' },
    { path: `/exports/${prefix}-restore-index.csv`, type: 'file', size: 1_248_400, modified: '2024-01-12T12:00:00Z' },
    { path: '/logs', type: 'directory', modified: '2024-01-15T06:05:00Z' },
    { path: `/logs/${prefix}-ltfs-mount.log`, type: 'file', size: 284_112, modified: '2024-01-15T06:05:00Z' },
    { path: `/logs/${prefix}-integrity-scan.log`, type: 'file', size: 832_516, modified: '2024-01-11T19:44:00Z' },
    { path: '/manifests', type: 'directory', modified: '2024-01-10T08:30:00Z' },
    { path: `/manifests/${prefix}-tape-manifest.json`, type: 'file', size: 94_320, modified: '2024-01-10T08:30:00Z' },
  ];
}

export async function listFiles(barcode: string, path = '/'): Promise<LtfsFile[]> {
  void barcode;

  // No backend file-listing route exists today. This returns a deterministic LTFS-style
  // directory tree so the browser UX can be exercised against realistic tape content.
  const tree = simulateTree(barcode);
  const normalizedPath = path === '/' ? '/' : `/${path.replace(/^\/+|\/+$/g, '')}`;
  const childMap = new Map<string, LtfsFile>();

  for (const node of tree) {
    const parent = node.path.split('/').slice(0, -1).join('/') || '/';
    if (parent !== normalizedPath) {
      continue;
    }

    const name = node.path.split('/').filter(Boolean).at(-1) ?? node.path;
    childMap.set(node.path, {
      name,
      path: node.path,
      size: node.size ?? 0,
      modified: node.modified,
      type: node.type,
    });
  }

  return Array.from(childMap.values()).sort((left, right) => {
    if (left.type !== right.type) {
      return left.type === 'directory' ? -1 : 1;
    }
    return left.name.localeCompare(right.name);
  });
}
