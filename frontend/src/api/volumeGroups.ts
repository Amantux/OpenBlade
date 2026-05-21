import type { VolumeGroup } from '../types/api';
import { rootApiRequest } from './client';

export function getVolumeGroups(): Promise<VolumeGroup[]> {
  return rootApiRequest<VolumeGroup[]>('/volume-groups/');
}

export function createVolumeGroup(name: string): Promise<VolumeGroup> {
  return rootApiRequest<VolumeGroup>('/volume-groups/', {
    method: 'POST',
    body: { name },
  });
}

export function assignVolumeGroupCartridge(name: string, barcode: string): Promise<VolumeGroup> {
  return rootApiRequest<VolumeGroup>(`/volume-groups/${encodeURIComponent(name)}/assign`, {
    method: 'POST',
    body: { barcode },
  });
}
