import type { DriveResponse } from '../../types/api';
import { cn, getDriveStateVariant, getMountStateVariant } from '../../lib/utils';
import Badge from '../ui/Badge';
import Card from '../ui/Card';

interface DrivePanelProps {
  drives: DriveResponse[];
}

export default function DrivePanel({ drives }: DrivePanelProps) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {drives.map((drive) => (
        <Card key={drive.drive_id} className="space-y-4 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm text-slate-400">Drive {drive.drive_id}</p>
              <p className="mt-1 text-lg font-semibold text-white">
                {drive.barcode ?? 'Empty'}
              </p>
            </div>
            <div className={cn('h-2.5 w-2.5 rounded-full bg-slate-500', drive.drive_state.toUpperCase() === 'BUSY' && 'animate-pulse bg-blue-400')} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge
              variant={getDriveStateVariant(drive.drive_state)}
              className={cn(drive.drive_state.toUpperCase() === 'BUSY' && 'animate-pulse')}
            >
              {drive.drive_state}
            </Badge>
            <Badge variant={getMountStateVariant(drive.mount_state)}>{drive.mount_state}</Badge>
          </div>
        </Card>
      ))}
    </div>
  );
}
