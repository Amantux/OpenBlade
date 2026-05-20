import { apiRequest, rootApiRequest } from './client';

export interface AmlSummary {
  overall: string;
  drives: {
    total: number;
    online: number;
    attention: number;
  };
  slots: {
    total: number;
    used: number;
    utilizationPercent: number;
  };
  jobs: {
    total: number;
    active: number;
    pending: number;
    completed: number;
    failed: number;
  };
  events: {
    total: number;
    critical: number;
    warning: number;
    info: number;
  };
  activeAlerts: number;
  openTickets: number;
}

export interface DashboardStats {
  storage: {
    totalFiles: number;
    totalBytes: number;
    volumeGroupCount: number;
    totalAssignedTapes: number;
    totalCatalogTapes: number;
    totalTapeCapacityBytes: number;
    availableTapeCapacityBytes: number;
    utilizationPercent: number;
  };
  volumeGroups: Array<{
    id: string;
    name: string;
    assignedTapes: number;
    fileCount: number;
    storedBytes: number;
  }>;
}

interface AmlSummaryEnvelope {
  summary: AmlSummary;
}

export async function getAmlSummary(): Promise<AmlSummary> {
  const response = await apiRequest<AmlSummaryEnvelope>('/summary');
  return response.summary;
}

export function getDashboardStats(): Promise<DashboardStats> {
  return rootApiRequest<DashboardStats>('/dashboard/stats');
}
