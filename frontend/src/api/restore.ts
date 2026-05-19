import type { EnqueuedJobResponse, RestoreRequestPayload } from '../types/api';
import { apiRequest } from './client';

export function postRestore(payload: RestoreRequestPayload): Promise<EnqueuedJobResponse> {
  return apiRequest<EnqueuedJobResponse>('/restore/', {
    method: 'POST',
    body: {
      catalog_path: payload.file_id,
      dest_path: payload.destination_path,
    },
  });
}
