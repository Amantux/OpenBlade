import type {
  ArchiveRequestPayload,
  EnqueuedJobResponse,
  ShardedArchiveRequestPayload,
} from '../types/api';
import { rootApiRequest } from './client';

export function postArchive(payload: ArchiveRequestPayload): Promise<EnqueuedJobResponse> {
  return rootApiRequest<EnqueuedJobResponse>('/archive/', {
    method: 'POST',
    body: payload,
  });
}

export function postShardedArchive(
  payload: ShardedArchiveRequestPayload,
): Promise<EnqueuedJobResponse> {
  return rootApiRequest<EnqueuedJobResponse>('/archive/sharded', {
    method: 'POST',
    body: payload,
  });
}
