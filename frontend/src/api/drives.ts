import { apiRequest } from './client';

interface WsResultCode {
  summary: string;
}

export function bringDriveOnline(serialNumber: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(serialNumber)}/online`, {
    method: 'POST',
  });
}

export function takeDriveOffline(serialNumber: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(serialNumber)}/offline`, {
    method: 'POST',
  });
}

export function unloadDrive(serialNumber: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/drive/${encodeURIComponent(serialNumber)}/unload`, {
    method: 'POST',
  });
}
