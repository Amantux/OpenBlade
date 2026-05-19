import type { HTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

export default function Card({ children, className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-md border border-quantum-border bg-quantum-info p-4 shadow-[0_12px_30px_rgba(0,0,0,0.22)]',
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}
