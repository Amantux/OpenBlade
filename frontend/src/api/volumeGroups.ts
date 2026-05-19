import type { VolumeGroup } from '../types/api';
import { apiRequest } from './client';

export function getVolumeGroups(): Promise<VolumeGroup[]> {
  return apiRequest<VolumeGroup[]>('/volume-groups/');
}
