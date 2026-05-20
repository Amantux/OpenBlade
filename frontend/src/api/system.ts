import type {
  DiagnosticsResponse,
  FirmwarePackageResponse,
  NetworkConfigResponse,
  SystemConfigResponse,
  SystemDetailResponse,
  SystemOverviewResponse,
  SystemStatusResponse,
  SystemVersionResponse,
  UptimeResponse,
} from '../types/api';
import { apiRequest } from './client';

export async function getSystemOverview(): Promise<SystemOverviewResponse> {
  const response = await apiRequest<{ systemInfo: SystemOverviewResponse }>('/system');
  return response.systemInfo;
}

export async function getSystemDetail(): Promise<SystemDetailResponse> {
  const response = await apiRequest<{ systemDetail: SystemDetailResponse }>('/system/info');
  return response.systemDetail;
}

export async function getSystemStatus(): Promise<SystemStatusResponse> {
  const response = await apiRequest<{ systemStatus: SystemStatusResponse }>('/system/status');
  return response.systemStatus;
}

export async function getSystemVersion(): Promise<SystemVersionResponse> {
  const response = await apiRequest<{ versionInfo: SystemVersionResponse }>('/system/version');
  return response.versionInfo;
}

export async function getSystemUptime(): Promise<UptimeResponse> {
  const response = await apiRequest<{ uptimeInfo: UptimeResponse }>('/system/uptime');
  return response.uptimeInfo;
}

export async function getNetworkConfig(): Promise<NetworkConfigResponse> {
  const response = await apiRequest<{ networkConfig: NetworkConfigResponse }>('/network');
  return response.networkConfig;
}

export async function getSystemConfig(): Promise<SystemConfigResponse> {
  const response = await apiRequest<{ systemConfig: SystemConfigResponse }>('/system/config');
  return response.systemConfig;
}

export async function getSystemFirmware(): Promise<{
  currentVersion: string;
  uploadedPackages: FirmwarePackageResponse[];
  status: {
    state: string;
    progress: number;
    message: string;
    currentVersion: string;
    stagedVersion?: string | null;
    lastUpdated?: string | null;
    lastActivated?: string | null;
  };
  lastActivated?: string | null;
}> {
  const response = await apiRequest<{ systemFirmware: {
    currentVersion: string;
    stagedPackage?: FirmwarePackageResponse | null;
    uploadedPackages: FirmwarePackageResponse[];
    status: {
      state: string;
      progress: number;
      message: string;
      currentVersion: string;
      stagedVersion?: string | null;
      lastUpdated?: string | null;
      lastActivated?: string | null;
    };
    lastActivated?: string | null;
  } }>('/system/firmware');
  return response.systemFirmware;
}

export async function getSystemDiagnostics(): Promise<DiagnosticsResponse> {
  const response = await apiRequest<{ diagnosticResult: DiagnosticsResponse }>('/system/diagnostics');
  return response.diagnosticResult;
}
