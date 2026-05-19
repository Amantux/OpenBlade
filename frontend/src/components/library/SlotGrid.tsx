import type { SlotResponse } from '../../types/api';
import { cn } from '../../lib/utils';

interface SlotGridProps {
  slots: SlotResponse[];
}

export default function SlotGrid({ slots }: SlotGridProps) {
  return (
    <div>
      <div
        className="grid gap-2"
        style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(28px, 28px))' }}
      >
        {slots.map((slot) => (
          <div
            key={slot.slot_id}
            title={slot.occupied ? `Slot ${slot.slot_id}: ${slot.barcode}` : `Slot ${slot.slot_id}: Empty`}
            className={cn(
              'h-7 w-7 rounded-md transition',
              slot.occupied
                ? 'bg-blue-600 hover:bg-blue-500'
                : 'border border-gray-600 bg-gray-700',
            )}
          />
        ))}
      </div>
      <div className="mt-4 flex items-center gap-4 text-xs text-slate-400">
        <span className="flex items-center gap-2">
          <span className="h-3 w-3 rounded bg-blue-600" /> Occupied
        </span>
        <span className="flex items-center gap-2">
          <span className="h-3 w-3 rounded border border-gray-600 bg-gray-700" /> Empty
        </span>
      </div>
    </div>
  );
}
