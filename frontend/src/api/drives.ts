import { apiRequest } from './client';

interface WsResultCode {
  summary: string;
}

export interface DriveLoadedMedia {
  barcode?: string | null;
  type?: string;
  state?: string;
  lastLoaded?: string | null;
}

export interface Drive {
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
  loadedMedia?: DriveLoadedMedia | null;
  interface?: string;
  generation?: number | string;
}

export interface DriveHealthStatus {
  overall: string;
  read: string;
  write: string;
  cleaning: string;
  connectivity: string;
}

export interface DriveStatistics {
  loadCount: number;
  unloadCount: number;
  errorCount: number;
  readErrors: number;
  writeErrors: number;
  cleaningCount: number;
  totalHours: number;
  lastLoaded?: string | null;
}

export interface DriveCleaningRecord {
  serialNumber: string;
  lastCleaned: string;
  mediaBarcode: string;
  useCount: number;
  expired: boolean;
}

interface DriveListResponse {
  driveList: {
    drive: Drive[];
  };
}

interface SingleDriveResponse {
  drive: Drive;
}

interface DriveStatusResponse {
  driveStatus: DriveHealthStatus;
}

interface DriveStatisticsResponse {
  driveStats: DriveStatistics;
}

interface DriveCleaningReportResponse {
  driveCleaningList: {
    driveCleaning: DriveCleaningRecord[];
  };
}

export async function listDrives(): Promise<Drive[]> {
  const response = await apiRequest<DriveListResponse>('/drives');
  return response.driveList.drive;
}

export async function getDrive(sn: string): Promise<Drive> {
  const response = await apiRequest<SingleDriveResponse>(`/drive/${encodeURIComponent(sn)}`);
  return response.drive;
}

export async function getDriveStatus(sn: string): Promise<DriveHealthStatus> {
  const response = await apiRequest<DriveStatusResponse>(`/drive/${encodeURIComponent(sn)}/status`);
  return response.driveStatus;
}

export async function getDriveStatistics(sn: string): Promise<DriveStatistics> {
  const response = await apiRequest<DriveStatisticsResponse>(`/drive/${encodeURIComponent(sn)}/statistics`);
  return response.driveStats;
}

export async function listDrivesNeedingCleaning(): Promise<Drive[]> {
  const response = await apiRequest<DriveListResponse>('/drives/cleaning');
  return response.driveList.drive;
}

export async function listDriveCleaningReports(): Promise<DriveCleaningRecord[]> {
  const response = await apiRequest<DriveCleaningReportResponse>('/drives/reports/cleaning');
  return response.driveCleaningList.driveCleaning;
}

export function bringDriveOnline(sn: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(sn)}/online`, {
    method: 'POST',
  });
}

export function takeDriveOffline(sn: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(sn)}/offline`, {
    method: 'POST',
  });
}

export function unloadDrive(sn: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(sn)}/unload`, {
    method: 'POST',
  });
}

export async function varyOnDrive(sn: string): Promise<void> {
  await bringDriveOnline(sn);
}

export async function varyOffDrive(sn: string): Promise<void> {
  await takeDriveOffline(sn);
}

export async function cleanDrive(sn: string): Promise<void> {
  await apiRequest<WsResultCode>(`/drive/${encodeURIComponent(sn)}/clean`, {
    method: 'POST',
  });
}
