import type { HealthResponse } from '../types/api';
import { apiRequest } from './client';

interface AmlSystemResponse {
  systemInfo: {
    hostname: string;
    firmware: string;
    uptime: number;
  };
}

interface AmlSystemStatusResponse {
  systemStatus: {
    overall: string;
  };
}

export async function getHealth(): Promise<HealthResponse> {
  const [system, status] = await Promise.all([
    apiRequest<AmlSystemResponse>('/system'),
    apiRequest<AmlSystemStatusResponse>('/system/status'),
  ]);

  return {
    status: status.systemStatus.overall,
    backend: 'AML API',
    hostname: system.systemInfo.hostname,
    firmware: system.systemInfo.firmware,
    uptime: system.systemInfo.uptime,
  };
}
