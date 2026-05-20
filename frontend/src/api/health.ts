import type {
  Alert,
  AuditEntry,
  Event,
  HealthResponse,
  RasTicket,
  SystemHealth,
} from '../types/api';
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

interface TicketEnvelope {
  ticketList: {
    ticket: Array<{
      id: string;
      timestamp: string;
      severity: string;
      status: string;
      component: string;
      description: string;
      resolution?: string | null;
      assignee?: string | null;
    }>;
  };
}

interface EventEnvelope {
  eventList: {
    event: Array<{
      id: string;
      timestamp: string;
      severity: string;
      component: string;
      message: string;
      details?: Record<string, unknown>;
    }>;
  };
}

interface AlertEnvelope {
  alertList: {
    alert: Alert[];
  };
}

interface HealthSummaryEnvelope {
  healthSummary: {
    overall: string;
    activeAlerts: number;
    openTickets: number;
    components: Record<string, string>;
  };
}

interface CapacityEnvelope {
  capacity: {
    totalSlots: number;
    usedSlots: number;
    totalDrives: number;
    activeDrives: number;
  };
}

interface JobEnvelope {
  jobList: {
    job: Array<{ id: string }>;
  };
}

interface BackupEnvelope {
  backupStatus: {
    lastBackup?: string | null;
    status?: string | null;
  };
}

interface AuditEnvelope {
  auditList: {
    audit: AuditEntry[];
  };
}

function mapHealthStatus(overall: string): string {
  switch (overall.toLowerCase()) {
    case 'good':
      return 'ONLINE';
    case 'warning':
      return 'DEGRADED';
    default:
      return 'OFFLINE';
  }
}

function formatUptime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return '0m';
  }

  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);

  if (days > 0) {
    return `${days}d ${hours}h`;
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${Math.max(minutes, 1)}m`;
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

export async function getRasTickets(): Promise<RasTicket[]> {
  const response = await apiRequest<TicketEnvelope>('/ras/tickets?limit=250');
  return response.ticketList.ticket.map((ticket) => ({
    id: ticket.id,
    severity: ticket.severity,
    component: ticket.component,
    message: ticket.description,
    opened: ticket.timestamp,
    state: ticket.status,
    resolution: ticket.resolution ?? null,
    assignee: ticket.assignee ?? null,
  }));
}

export async function acknowledgeTicket(id: string): Promise<void> {
  await apiRequest(`/ras/ticket/${encodeURIComponent(id)}/acknowledge`, { method: 'POST' });
}

export async function getEvents(limit = 100): Promise<Event[]> {
  const response = await apiRequest<EventEnvelope>(`/events?limit=${limit}`);
  return response.eventList.event.map((event) => ({
    id: event.id,
    timestamp: event.timestamp,
    severity: event.severity,
    component: event.component,
    message: event.message,
    details: event.details ?? {},
  }));
}

export async function getAlerts(): Promise<Alert[]> {
  const response = await apiRequest<AlertEnvelope>('/alerts');
  return response.alertList.alert;
}

export async function dismissAlert(id: string): Promise<void> {
  await apiRequest(`/alert/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function getSystemHealth(): Promise<SystemHealth> {
  const [summary, system, capacity, jobs, backup] = await Promise.all([
    apiRequest<HealthSummaryEnvelope>('/health'),
    apiRequest<AmlSystemResponse>('/system'),
    apiRequest<CapacityEnvelope>('/operations/capacity'),
    apiRequest<JobEnvelope>('/jobs'),
    apiRequest<BackupEnvelope>('/system/backup'),
  ]);

  return {
    overall: mapHealthStatus(summary.healthSummary.overall),
    drivesOnline: capacity.capacity.activeDrives,
    drivesTotal: capacity.capacity.totalDrives,
    slotsUsed: capacity.capacity.usedSlots,
    slotsTotal: capacity.capacity.totalSlots,
    activeJobs: jobs.jobList.job.length,
    openTickets: summary.healthSummary.openTickets,
    uptime: system.systemInfo.uptime,
    uptimeFormatted: formatUptime(system.systemInfo.uptime),
    lastBackupTime: backup.backupStatus.lastBackup ?? null,
    lastBackupStatus: backup.backupStatus.status ?? null,
    backend: 'AML API',
    activeAlerts: summary.healthSummary.activeAlerts,
    componentStates: summary.healthSummary.components,
  };
}

export async function getAuditLog(limit = 200): Promise<AuditEntry[]> {
  const response = await apiRequest<AuditEnvelope>(`/system/audit?limit=${limit}`);
  return response.auditList.audit;
}
