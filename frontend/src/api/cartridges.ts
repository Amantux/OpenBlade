import type { CartridgeResponse } from '../types/api';
import { apiRequest } from './client';

export function getCartridges(): Promise<CartridgeResponse[]> {
  return apiRequest<CartridgeResponse[]>('/cartridges/');
}
