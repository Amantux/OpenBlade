import { apiRequest } from './client';

export type RbacPermission =
  | 'tape:read'
  | 'tape:write'
  | 'tape:format'
  | 'tape:eject'
  | 'nas:read'
  | 'nas:write'
  | 'nas:admin'
  | 'catalog:read'
  | 'catalog:rebuild'
  | 'user:admin'
  | 'token:manage'
  | 'audit:read'
  | 'system:admin';

export interface AdminUser {
  id: string;
  username: string;
  role_id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface CreateAdminUserRequest {
  username: string;
  password: string;
  role_id: string;
  email?: string;
  full_name?: string;
  is_admin?: boolean;
}

export interface AdminRole {
  id: string;
  name: string;
  description: string;
  permissions: RbacPermission[];
  created_at: string;
  updated_at: string;
}

export interface CreateAdminRoleRequest {
  id: string;
  name: string;
  description?: string;
  permissions: RbacPermission[];
}

export interface AdminToken {
  id: string;
  user_id: string;
  name: string;
  token_hash: string;
  permissions: RbacPermission[];
  expires_at: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
}

export interface CreateAdminTokenRequest {
  name: string;
  permissions: RbacPermission[];
  expires_at?: string | null;
}

export interface CreateAdminTokenResponse {
  token_id: string;
  raw_token: string;
  token_record: AdminToken;
}

export interface AdminAuditEvent {
  id: string;
  event_type: string;
  user_id: string | null;
  username: string;
  resource: string;
  action: string;
  outcome: string;
  details: Record<string, unknown>;
  created_at: string;
  ip_address: string | null;
}

export interface WsResultCode {
  summary: string;
}

export function listAdminUsers(): Promise<AdminUser[]> {
  return apiRequest<AdminUser[]>('/auth/users');
}

export function createAdminUser(payload: CreateAdminUserRequest): Promise<AdminUser> {
  return apiRequest<AdminUser>('/auth/users', {
    method: 'POST',
    body: payload,
  });
}

export function deleteAdminUser(userId: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/auth/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
  });
}

export function listAdminRoles(): Promise<AdminRole[]> {
  return apiRequest<AdminRole[]>('/auth/roles');
}

export function createAdminRole(payload: CreateAdminRoleRequest): Promise<AdminRole> {
  return apiRequest<AdminRole>('/auth/roles', {
    method: 'POST',
    body: payload,
  });
}

export function listAdminTokens(): Promise<AdminToken[]> {
  return apiRequest<AdminToken[]>('/auth/tokens');
}

export function createAdminToken(payload: CreateAdminTokenRequest): Promise<CreateAdminTokenResponse> {
  return apiRequest<CreateAdminTokenResponse>('/auth/tokens', {
    method: 'POST',
    body: payload,
  });
}

export function revokeAdminToken(tokenId: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>(`/auth/tokens/${encodeURIComponent(tokenId)}`, {
    method: 'DELETE',
  });
}

export function listAdminAuditEvents(limit = 100): Promise<AdminAuditEvent[]> {
  return apiRequest<AdminAuditEvent[]>(`/auth/audit?limit=${limit}`);
}

export const ALL_RBAC_PERMISSIONS: RbacPermission[] = [
  'tape:read',
  'tape:write',
  'tape:format',
  'tape:eject',
  'nas:read',
  'nas:write',
  'nas:admin',
  'catalog:read',
  'catalog:rebuild',
  'user:admin',
  'token:manage',
  'audit:read',
  'system:admin',
];
