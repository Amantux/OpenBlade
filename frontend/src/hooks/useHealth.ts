import { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getHealth } from '../api/health';

export function useHealth(refetchInterval = 10_000) {
  const latenciesRef = useRef<number[]>([]);
  const [latencyVersion, setLatencyVersion] = useState(0);

  const query = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const startedAt = Date.now();
      const health = await getHealth();
      const elapsed = Date.now() - startedAt;
      latenciesRef.current = [...latenciesRef.current, elapsed].slice(-30);
      setLatencyVersion((current) => current + 1);
      return health;
    },
    refetchInterval,
    refetchIntervalInBackground: false,
  });

  const latencies = useMemo(
    () => latenciesRef.current.map((latency, index) => ({ index, latency })),
    [latencyVersion],
  );
  const avgLatency = useMemo(() => {
    if (latenciesRef.current.length === 0) {
      return 0;
    }
    const total = latenciesRef.current.reduce((sum, current) => sum + current, 0);
    return Math.round(total / latenciesRef.current.length);
  }, [latencyVersion]);

  return {
    ...query,
    health: query.data,
    latencies,
    avgLatency,
  };
}
