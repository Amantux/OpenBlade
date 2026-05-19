import Badge from '../ui/Badge';

interface ChangerBadgeProps {
  state: string;
}

export default function ChangerBadge({ state }: ChangerBadgeProps) {
  const normalized = state.toUpperCase();
  const variant = normalized === 'IDLE' ? 'green' : normalized === 'FAULTED' ? 'red' : 'amber';
  return <Badge variant={variant}>{state}</Badge>;
}
