import { apiRequest } from './client';

interface WsResultCode {
  summary: string;
}

export function login(name: string, password: string): Promise<WsResultCode> {
  return apiRequest<WsResultCode>('/users/login', {
    method: 'POST',
    body: { name, password },
    skipAuthRedirect: true,
  });
}

export function logout(): Promise<WsResultCode> {
  return apiRequest<WsResultCode>('/users/login', {
    method: 'DELETE',
  });
}
