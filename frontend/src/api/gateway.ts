import { rootApiRequest } from './client';

export interface GatewayConfig {
  bind_host: string;
  bind_port: number;
  max_sessions: number;
  inbox_root: string;
  status: string;
  last_error: string | null;
}

export interface GatewayStats {
  status: string;
  active_sessions: number;
  total_sessions: number;
  total_bytes_uploaded: number;
  total_files_uploaded: number;
  credentials_count: number;
  last_error: string | null;
}

export interface GatewayCommandResponse {
  status: string;
  message: string;
  last_error: string | null;
}

export interface GatewayCredential {
  username: string;
  enabled: boolean;
  allowed_paths: string[];
  active_sessions?: number;
}

export interface GatewaySession {
  session_id: string;
  username: string;
  remote_addr: string;
  connected_at: string;
  disconnected_at: string | null;
  bytes_uploaded: number;
  files_uploaded: number;
  errors: number;
  uploads: {
    requested_path: string;
    routed_path: string;
    bytes_uploaded: number;
    uploaded_at: string;
  }[];
}

export interface InboxPathOption {
  path: string;
  description: string;
}

export interface CredentialCreate {
  username: string;
  password: string;
  allowed_paths?: string[];
}

export interface CredentialUpdate {
  password?: string;
  enabled?: boolean;
  allowed_paths?: string[];
}

export const getGatewayConfig = () =>
  rootApiRequest<GatewayConfig>('/api/gateway/config');

export const getGatewayStatus = () =>
  rootApiRequest<GatewayStats>('/api/gateway/status');

export const startGateway = () =>
  rootApiRequest<GatewayCommandResponse>('/api/gateway/start', { method: 'POST' });

export const stopGateway = () =>
  rootApiRequest<GatewayCommandResponse>('/api/gateway/stop', { method: 'POST' });

export const listCredentials = () =>
  rootApiRequest<GatewayCredential[]>('/api/gateway/credentials');

export const addCredential = (data: CredentialCreate) =>
  rootApiRequest<GatewayCredential>('/api/gateway/credentials', {
    method: 'POST',
    body: data,
  });

export const updateCredential = (username: string, data: CredentialUpdate) =>
  rootApiRequest<GatewayCredential>(`/api/gateway/credentials/${encodeURIComponent(username)}`, {
    method: 'PUT',
    body: data,
  });

export const removeCredential = (username: string) =>
  rootApiRequest<{ deleted: string }>(`/api/gateway/credentials/${encodeURIComponent(username)}`, {
    method: 'DELETE',
  });

export const listSessions = (activeOnly = false) =>
  rootApiRequest<GatewaySession[]>(`/api/gateway/sessions?active_only=${activeOnly}`);

export const listInboxPaths = () =>
  rootApiRequest<InboxPathOption[]>('/api/gateway/inbox-paths');
