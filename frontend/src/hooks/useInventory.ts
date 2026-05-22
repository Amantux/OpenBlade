import { useQuery } from '@tanstack/react-query';
import { getInventory } from '../api/inventory';

export function useInventory(libraryId = '') {
  return useQuery({
    queryKey: ['inventory', libraryId],
    queryFn: getInventory,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  });
}
