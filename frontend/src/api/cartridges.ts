import type { CartridgeResponse, MediaPoolResponse } from '../types/api';
import { apiRequest } from './client';

interface AmlMediaListResponse {
  mediaList: {
    media: CartridgeResponse[];
  };
}

interface AmlMediaPoolListResponse {
  poolList: {
    pool: MediaPoolResponse[];
  };
}

export async function getCartridges(): Promise<CartridgeResponse[]> {
  const response = await apiRequest<AmlMediaListResponse>('/media');
  return response.mediaList.media;
}

export async function getMediaPools(): Promise<MediaPoolResponse[]> {
  const response = await apiRequest<AmlMediaPoolListResponse>('/media/pools');
  return response.poolList.pool;
}
