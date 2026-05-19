import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '../../lib/utils';

type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';

const variantClasses: Record<ButtonVariant, string> = {
  primary: 'border border-quantum-red bg-quantum-red text-white hover:bg-quantum-red-hover hover:border-quantum-red-hover',
  secondary: 'border border-quantum-border bg-quantum-sidebar text-slate-100 hover:bg-quantum-north',
  danger: 'border border-red-700 bg-red-900/60 text-red-100 hover:bg-red-800/70',
  ghost: 'border border-transparent bg-transparent text-slate-200 hover:bg-quantum-north',
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  children: ReactNode;
  variant?: ButtonVariant;
}

export default function Button({
  children,
  className,
  variant = 'primary',
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50',
        variantClasses[variant],
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
