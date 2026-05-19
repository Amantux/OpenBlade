import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import HealthPanel from '../components/health/HealthPanel';
import Card from '../components/ui/Card';
import { useHealth } from '../hooks/useHealth';
import { useInventory } from '../hooks/useInventory';
import { deriveSystemHealth } from '../lib/utils';

export default function Health() {
  const healthQuery = useHealth();
  const inventoryQuery = useInventory();

  if (healthQuery.isLoading || inventoryQuery.isLoading) {
    return <Spinner />;
  }
  if (healthQuery.isError) {
    return <ErrorMessage error={healthQuery.error} onRetry={() => healthQuery.refetch()} />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }

  const inventory = inventoryQuery.data ?? { library_id: 'unknown', slots: [], drives: [], changer_state: 'unknown' };
  const status = deriveSystemHealth(healthQuery.health, inventory.drives, inventory.changer_state);

  return (
    <div className="space-y-6">
      <HealthPanel
        health={healthQuery.health}
        inventory={inventory}
        status={status}
        avgLatency={healthQuery.avgLatency}
        latencies={healthQuery.latencies}
      />
      <Card className="text-sm text-slate-300">
        Polling cadence: dashboard every 10s, job detail every 2s while active, with background refresh paused when the window loses focus.
      </Card>
    </div>
  );
}
