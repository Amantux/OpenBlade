import type {
  DiagnosticsResponse,
  NetworkConfigResponse,
  SystemConfigResponse,
  SystemDetailResponse,
  SystemOverviewResponse,
  SystemStatusResponse,
  SystemVersionResponse,
  UptimeResponse,
} from '../types/api';
import { apiRequest } from './client';

export interface SystemTimeResponse {
  utc: string;
  local: string;
  timezone: string;
  ntp: boolean;
}

export interface NetworkInterfaceResponse {
  name: string;
  type: string;
  ip: string;
  mask: string;
  gateway: string;
  mac: string;
  status: string;
  speed: string;
  duplex: string;
}

export interface DnsConfigResponse {
  primary: string;
  secondary: string;
  search: string[];
  domain: string;
}

export interface NtpConfigResponse {
  enabled: boolean;
  servers: string[];
  status: string;
  lastSync?: string | null;
}

export interface RouteEntry {
  destination: string;
  mask: string;
  gateway: string;
  interface: string;
  metric: number;
}

export interface SecurityConfigResponse {
  tlsEnabled: boolean;
  tlsVersion: string;
  cipherSuites: string[];
  certExpiry?: string | null;
  sshEnabled: boolean;
  loginBanner: string;
}

export interface CertificateSummaryResponse {
  name: string;
  subject: string;
  expiry?: string | null;
  status: string;
}

export interface CertificateCreateRequest {
  name: string;
  pem: string;
}

interface WsResultResponse {
  summary: string;
}

export interface SnmpConfigResponse {
  enabled: boolean;
  version: string;
  community: string;
  trapHosts: string[];
  contact: string;
  location: string;
}

export interface SnmpTrapResponse {
  timestamp: string;
  oid: string;
  value: string;
  host: string;
}

export interface EmailConfigResponse {
  enabled: boolean;
  smtpHost: string;
  smtpPort: number;
  smtpUser: string;
  from: string;
  tls: boolean;
  recipients: string[];
}

export interface BackupStatusResponse {
  lastBackup?: string | null;
  location?: string | null;
  size: number;
  status: string;
}

export interface FirmwarePackageResponse {
  name: string;
  version: string;
  size: number;
  uploadedAt: string;
  checksum?: string | null;
  active: boolean;
}

export interface SystemFirmwareResponse {
  currentVersion: string;
  stagedVersion?: string | null;
  stagedPackage?: string | null;
  status: string;
  lastActivated?: string | null;
  uploadedPackages: FirmwarePackageResponse[];
}

export interface SystemFirmwareStatusResponse {
  state: string;
  progress: number;
  message: string;
  currentVersion: string;
  stagedVersion?: string | null;
  lastUpdated: string;
  lastActivated?: string | null;
}

export interface SystemUpdateResponse {
  name: string;
  version: string;
  description: string;
  type: string;
  size: number;
}

export interface EthBladeResponse {
  id: string;
  serialNumber: string;
  model: string;
  status: string;
  firmware: string;
  portCount: number;
}

export interface FcBladeResponse {
  id: string;
  serialNumber: string;
  model: string;
  status: string;
  firmware: string;
  portCount: number;
}

export interface MgmtBladeResponse {
  id: string;
  serialNumber: string;
  model: string;
  status: string;
  firmware: string;
  role: string;
}

export interface BladeFirmwareResponse {
  name: string;
  target: string;
  version: string;
  status: string;
  uploadedAt: string;
  size: number;
  checksum?: string | null;
}

export interface DriveResponse {
  serialNumber: string;
  model: string;
  type: string;
  status: string;
  state: string;
  partition: string;
  location: string;
  firmware: string;
  loadCount: number;
  errorCount: number;
  cleaningCount: number;
  lastCleaned?: string | null;
}

export interface DiagnosticTestDefinition {
  id: string;
  name: string;
  description: string;
  category: string;
  estimatedDuration: number;
}

export interface DiagnosticResultResponse {
  id: string;
  testId: string;
  startTime: string;
  endTime: string;
  status: string;
  passed: number;
  failed: number;
  details: Array<{
    name: string;
    status: string;
    message: string;
  }>;
}

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

export async function getSystemTime(): Promise<SystemTimeResponse> {
  const response = await apiRequest<{ systemTime: SystemTimeResponse }>('/system/time');
  return response.systemTime;
}

export async function updateSystemTime(utc: string): Promise<void> {
  await apiRequest('/system/time', { method: 'PUT', body: { systemTime: { utc } } });
}

export async function getNetworkConfig(): Promise<NetworkConfigResponse> {
  const response = await apiRequest<{ networkConfig: NetworkConfigResponse }>('/network');
  return response.networkConfig;
}

export async function getNetworkInterfaces(): Promise<NetworkInterfaceResponse[]> {
  const response = await apiRequest<{ interfaceList: { interface: NetworkInterfaceResponse[] } }>('/network/interfaces');
  return response.interfaceList.interface;
}

export async function updateNetworkInterface(name: string, payload: Partial<Pick<NetworkInterfaceResponse, 'ip' | 'mask' | 'gateway' | 'duplex'>>): Promise<NetworkInterfaceResponse> {
  const response = await apiRequest<{ interface: NetworkInterfaceResponse }>(`/network/interface/${name}`, {
    method: 'PUT',
    body: { interface: payload },
  });
  return response.interface;
}

export async function getDnsConfig(): Promise<DnsConfigResponse> {
  const response = await apiRequest<{ dnsConfig: DnsConfigResponse }>('/network/dns');
  return response.dnsConfig;
}

export async function updateDnsConfig(payload: Partial<DnsConfigResponse>): Promise<DnsConfigResponse> {
  const response = await apiRequest<{ dnsConfig: DnsConfigResponse }>('/network/dns', {
    method: 'PUT',
    body: { dnsConfig: payload },
  });
  return response.dnsConfig;
}

export async function getNetworkRoutes(): Promise<RouteEntry[]> {
  const response = await apiRequest<{ routeList: { route: RouteEntry[] } }>('/network/routing');
  return response.routeList.route;
}

export async function getNtpConfig(): Promise<NtpConfigResponse> {
  const response = await apiRequest<{ ntpConfig: NtpConfigResponse }>('/network/ntp');
  return response.ntpConfig;
}

export async function updateNtpConfig(payload: Partial<NtpConfigResponse>): Promise<NtpConfigResponse> {
  const response = await apiRequest<{ ntpConfig: NtpConfigResponse }>('/network/ntp', {
    method: 'PUT',
    body: { ntpConfig: payload },
  });
  return response.ntpConfig;
}

export async function syncNtp(): Promise<void> {
  await apiRequest('/network/ntp/sync', { method: 'POST' });
}

export async function getSystemConfig(): Promise<SystemConfigResponse> {
  const response = await apiRequest<{ systemConfig: SystemConfigResponse }>('/system/config');
  return response.systemConfig;
}

export async function updateSystemConfig(payload: Partial<SystemConfigResponse>): Promise<SystemConfigResponse> {
  const response = await apiRequest<{ systemConfig: SystemConfigResponse }>('/system/config', {
    method: 'PUT',
    body: { systemConfig: payload },
  });
  return response.systemConfig;
}

export async function getSystemSecurity(): Promise<SecurityConfigResponse> {
  const response = await apiRequest<{ securityConfig: SecurityConfigResponse }>('/system/security');
  return response.securityConfig;
}

export async function updateSystemSecurity(payload: Partial<SecurityConfigResponse>): Promise<SecurityConfigResponse> {
  const response = await apiRequest<{ securityConfig: SecurityConfigResponse }>('/system/security', {
    method: 'PUT',
    body: { securityConfig: payload },
  });
  return response.securityConfig;
}

export async function getSystemCertificates(): Promise<CertificateSummaryResponse[]> {
  const response = await apiRequest<{ certList: { cert: CertificateSummaryResponse[] } }>('/system/certificates');
  return response.certList.cert;
}

export async function createSystemCertificate(payload: CertificateCreateRequest): Promise<string> {
  const name = payload.name.trim();
  const pem = payload.pem.trim();

  if (!name) {
    throw new Error('Certificate name is required.');
  }
  if (!pem) {
    throw new Error('PEM content is required.');
  }

  const expiry = new Date(Date.now() + 365 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  const response = await apiRequest<WsResultResponse>('/system/certificate/import', {
    method: 'POST',
    body: {
      cert: {
        name,
        subject: `CN=${name},O=OpenBlade Upload`,
        expiry,
        status: 'valid',
        type: 'uploaded',
      },
    },
  });

  return response.summary;
}

export async function deleteSystemCertificate(name: string): Promise<string> {
  const response = await apiRequest<WsResultResponse>(`/system/certificate/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  });
  return response.summary;
}

export async function getSnmpConfig(): Promise<SnmpConfigResponse> {
  const response = await apiRequest<{ snmpConfig: SnmpConfigResponse }>('/system/snmp');
  return response.snmpConfig;
}

export async function updateSnmpConfig(payload: Partial<SnmpConfigResponse>): Promise<SnmpConfigResponse> {
  const response = await apiRequest<{ snmpConfig: SnmpConfigResponse }>('/system/snmp', {
    method: 'PUT',
    body: { snmpConfig: payload },
  });
  return response.snmpConfig;
}

export async function testSnmp(): Promise<void> {
  await apiRequest('/system/snmp/test', { method: 'POST' });
}

export async function getSnmpTraps(): Promise<SnmpTrapResponse[]> {
  const response = await apiRequest<{ trapList: { trap: SnmpTrapResponse[] } }>('/system/snmp/traps');
  return response.trapList.trap;
}

export async function getEmailConfig(): Promise<EmailConfigResponse> {
  const response = await apiRequest<{ emailConfig: EmailConfigResponse }>('/system/email');
  return response.emailConfig;
}

export async function updateEmailConfig(payload: Partial<EmailConfigResponse>): Promise<EmailConfigResponse> {
  const response = await apiRequest<{ emailConfig: EmailConfigResponse }>('/system/email', {
    method: 'PUT',
    body: { emailConfig: payload },
  });
  return response.emailConfig;
}

export async function testEmail(): Promise<void> {
  await apiRequest('/system/email/test', { method: 'POST' });
}

export async function getBackupStatus(): Promise<BackupStatusResponse> {
  const response = await apiRequest<{ backupStatus: BackupStatusResponse }>('/system/backup');
  return response.backupStatus;
}

export async function getSystemUpdates(): Promise<SystemUpdateResponse[]> {
  const response = await apiRequest<{ updateList: { update: SystemUpdateResponse[] } }>('/system/updates');
  return response.updateList.update;
}

export async function getSystemFirmware(): Promise<SystemFirmwareResponse> {
  const response = await apiRequest<{
    systemFirmware: {
      currentVersion: string;
      stagedVersion?: string | null;
      stagedPackage?: string | null;
      status: string;
      lastActivated?: string | null;
      package: FirmwarePackageResponse[];
    };
  }>('/aml/system/firmware');

  return {
    currentVersion: response.systemFirmware.currentVersion,
    stagedVersion: response.systemFirmware.stagedVersion,
    stagedPackage: response.systemFirmware.stagedPackage,
    status: response.systemFirmware.status,
    lastActivated: response.systemFirmware.lastActivated,
    uploadedPackages: response.systemFirmware.package,
  };
}

export async function getSystemFirmwareStatus(): Promise<SystemFirmwareStatusResponse> {
  const response = await apiRequest<{ firmwareStatus: SystemFirmwareStatusResponse }>('/aml/system/firmware/status');
  return response.firmwareStatus;
}

export async function uploadSystemFirmware(file: File): Promise<void> {
  const formData = new FormData();
  formData.append('file', file);
  await apiRequest('/aml/system/firmware', { method: 'POST', body: formData });
}

export async function activateSystemFirmware(): Promise<void> {
  await apiRequest('/aml/system/firmware/activate', { method: 'PUT', body: { firmware: { commit: true } } });
}

export async function getEthBlades(): Promise<EthBladeResponse[]> {
  const response = await apiRequest<{ ethBladeList: { ethBlade: EthBladeResponse[] } }>('/devices/ethBlades');
  return response.ethBladeList.ethBlade;
}

export async function getFcBlades(): Promise<FcBladeResponse[]> {
  const response = await apiRequest<{ fcBladeList: { fcBlade: FcBladeResponse[] } }>('/devices/fcBlades');
  return response.fcBladeList.fcBlade;
}

export async function getMgmtBlades(): Promise<MgmtBladeResponse[]> {
  const response = await apiRequest<{ mgmtBladeList: { mgmtBlade: MgmtBladeResponse[] } }>('/devices/mgmtBlades');
  return response.mgmtBladeList.mgmtBlade;
}

export async function getDrives(): Promise<DriveResponse[]> {
  const response = await apiRequest<{ driveList: { drive: DriveResponse[] } }>('/drives');
  return response.driveList.drive;
}

export async function getBladeFirmware(): Promise<BladeFirmwareResponse[]> {
  const response = await apiRequest<{ bladeFirmwareList: { firmware: BladeFirmwareResponse[] } }>('/devices/blades/firmware');
  return response.bladeFirmwareList.firmware;
}

export async function getSystemDiagnostics(): Promise<DiagnosticsResponse> {
  const response = await apiRequest<{ diagResult: DiagnosticsResponse }>('/system/diagnostics');
  return response.diagResult;
}

export async function getDiagnosticTests(): Promise<DiagnosticTestDefinition[]> {
  const response = await apiRequest<{ diagnosticTestList: { diagnosticTest: DiagnosticTestDefinition[] } }>('/diagnostics/tests');
  return response.diagnosticTestList.diagnosticTest;
}

export async function runDiagnosticTests(testIds: string[] = []): Promise<void> {
  await apiRequest('/diagnostics/tests/run', {
    method: 'POST',
    body: { testIds, suiteName: testIds.length ? undefined : 'full-suite' },
  });
}

export async function getDiagnosticResults(): Promise<DiagnosticResultResponse> {
  const response = await apiRequest<{ diagnosticResult: DiagnosticResultResponse }>('/diagnostics/tests/results');
  return response.diagnosticResult;
}
