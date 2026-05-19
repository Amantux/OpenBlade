import type {
  ArchiveRequestPayload,
  EnqueuedJobResponse,
  ShardedArchiveRequestPayload,
} from '../types/api';
import { apiRequest } from './client';

export function postArchive(payload: ArchiveRequestPayload): Promise<EnqueuedJobResponse> {
  return apiRequest<EnqueuedJobResponse>('/archive/', {
    method: 'POST',
    body: payload,
  });
}

export function postShardedArchive(
  payload: ShardedArchiveRequestPayload,
): Promise<EnqueuedJobResponse> {
  return apiRequest<EnqueuedJobResponse>('/archive/sharded', {
    method: 'POST',
    body: payload,
  });
}
