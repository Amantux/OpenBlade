import { cn, formatBytes } from '../../lib/utils';

interface BytesDisplayProps {
  value: number;
  className?: string;
}

export default function BytesDisplay({ value, className }: BytesDisplayProps) {
  return <span className={cn('font-mono text-sm text-slate-100', className)}>{formatBytes(value)}</span>;
}
