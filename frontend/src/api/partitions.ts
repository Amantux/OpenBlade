import type { PartitionResponse } from '../types/api';
import { apiRequest } from './client';

interface AmlPartitionListResponse {
  partitionList: {
    partition: PartitionResponse[];
  };
}

export async function getPartitions(): Promise<PartitionResponse[]> {
  const response = await apiRequest<AmlPartitionListResponse>('/partitions');
  return response.partitionList.partition;
}
