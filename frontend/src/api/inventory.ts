import type { DriveResponse, InventoryResponse } from '../types/api';
import { apiRequest } from './client';

export function getInventory(): Promise<InventoryResponse> {
  return apiRequest<InventoryResponse>('/inventory/');
}

export async function getDrives(): Promise<DriveResponse[]> {
  const inventory = await getInventory();
  return inventory.drives;
}
