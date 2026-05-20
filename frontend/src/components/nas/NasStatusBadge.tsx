import Badge from '../ui/Badge';
import { toTitleCase, type BadgeVariant } from '../../lib/utils';

interface NasStatusBadgeProps {
  value: string;
  label?: string;
  className?: string;
}

function toVariant(value: string): BadgeVariant {
  switch (value.toLowerCase()) {
    case 'critical_sequential':
    case 'critical':
    case 'failed':
    case 'error':
    case 'not safe':
    case 'missing_tape':
    case 'corrupt':
      return 'red';
    case 'noncritical_sharded':
    case 'warning':
    case 'after_days':
    case 'manual':
    case 'watch':
    case 'hydrating':
    case 'paused':
      return 'amber';
    case 'balanced':
    case 'cache_drive':
    case 'online_cached':
    case 'completed':
    case 'safe':
    case 'healthy':
    case 'verified':
    case 'archived':
    case 'read_write':
    case 'running':
      return 'green';
    case 'source_stream':
    case 'streaming':
    case 'planning':
    case 'archiving':
      return 'blue';
    case 'offline_on_tape':
    case 'manual_only':
    case 'disabled':
    case 'queued':
    case 'cancelled':
    case 'pending':
    case 'read_only':
      return 'gray';
    case 'exported':
      return 'purple';
    default:
      return 'gray';
  }
}

export default function NasStatusBadge({ value, label, className }: NasStatusBadgeProps) {
  return (
    <Badge variant={toVariant(value)} className={className}>
      {label ?? toTitleCase(value)}
    </Badge>
  );
}
