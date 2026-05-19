import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

export default function Card({ children, className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-2xl border border-slate-800 bg-blade-900/80 p-5 shadow-lg shadow-black/10 backdrop-blur',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
