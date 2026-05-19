import ChangerBadge from '../components/library/ChangerBadge';
import DrivePanel from '../components/library/DrivePanel';
import SlotGrid from '../components/library/SlotGrid';
import ErrorMessage from '../components/ui/ErrorMessage';
import Card from '../components/ui/Card';
import Spinner from '../components/ui/Spinner';
import { useInventory } from '../hooks/useInventory';

export default function Library() {
  const inventoryQuery = useInventory();

  if (inventoryQuery.isLoading) {
    return <Spinner />;
  }
  if (inventoryQuery.isError) {
    return <ErrorMessage error={inventoryQuery.error} onRetry={() => inventoryQuery.refetch()} />;
  }

  const inventory = inventoryQuery.data ?? { library_id: 'unknown', slots: [], drives: [], changer_state: 'unknown' };

  return (
    <div className="space-y-6">
      <Card className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.25em] text-slate-500">Library</p>
          <h2 className="mt-1 text-xl font-semibold text-white">{inventory.library_id}</h2>
        </div>
        <div className="flex items-center gap-3 text-sm text-slate-300">
          <span>Changer state</span>
          <ChangerBadge state={inventory.changer_state} />
        </div>
      </Card>
      <Card className="space-y-4">
        <div>
          <h2 className="text-lg font-semibold text-white">Slot topology</h2>
          <p className="text-sm text-slate-400">Hover a slot to inspect slot id and barcode.</p>
        </div>
        <SlotGrid slots={inventory.slots} />
      </Card>
      <div>
        <div className="mb-4">
          <h2 className="text-lg font-semibold text-white">Drive bays</h2>
          <p className="text-sm text-slate-400">Monitor tape occupancy, drive state, and mount state per bay.</p>
        </div>
        <DrivePanel drives={inventory.drives} />
      </div>
    </div>
  );
}
