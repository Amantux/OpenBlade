import type { VolumeGroup } from '../types/api';
import { rootApiRequest } from './client';

export function getVolumeGroups(): Promise<VolumeGroup[]> {
  return rootApiRequest<VolumeGroup[]>('/volume-groups/');
}
