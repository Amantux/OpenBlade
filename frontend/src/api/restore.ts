import type { EnqueuedJobResponse, RestoreRequestPayload } from '../types/api';
import { rootApiRequest } from './client';

export function postRestore(payload: RestoreRequestPayload): Promise<EnqueuedJobResponse> {
  return rootApiRequest<EnqueuedJobResponse>('/restore/', {
    method: 'POST',
    body: {
      catalog_path: payload.file_id,
      dest_path: payload.destination_path,
    },
  });
}
