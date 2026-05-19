import type { ReactNode } from 'react';
import { cn, type BadgeVariant } from '../../lib/utils';

const variantClasses: Record<BadgeVariant, string> = {
  gray: 'border-slate-700 bg-slate-800/90 text-slate-200',
  blue: 'border-blue-500/30 bg-blue-500/15 text-blue-300',
  green: 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300',
  amber: 'border-amber-500/30 bg-amber-500/15 text-amber-300',
  red: 'border-red-500/30 bg-red-500/15 text-red-300',
  redDim: 'border-red-900/50 bg-red-950/30 text-red-400',
};

interface BadgeProps {
  children: ReactNode;
  variant?: BadgeVariant;
  className?: string;
}

export default function Badge({ children, variant = 'gray', className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium tracking-wide',
        variantClasses[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
