import type { HealthResponse } from '../types/api';
import { apiRequest } from './client';

export function getHealth(): Promise<HealthResponse> {
  return apiRequest<HealthResponse>('/health');
}
