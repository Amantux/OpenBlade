import { useQuery } from '@tanstack/react-query';
import { getInventory } from '../api/inventory';

export function useInventory() {
  return useQuery({
    queryKey: ['inventory'],
    queryFn: getInventory,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
  });
}
