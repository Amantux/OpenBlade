import type { ReactNode } from 'react';
import { cn } from '../../lib/utils';

export interface NorthPanelColumn<T> {
  key: string;
  header: string;
  className?: string;
  render: (row: T) => ReactNode;
}

interface NorthPanelProps<T> {
  title: string;
  subtitle?: string;
  columns: NorthPanelColumn<T>[];
  rows: T[];
  getRowId: (row: T) => string;
  selectedId?: string;
  onSelect?: (row: T) => void;
  emptyMessage?: string;
}

export default function NorthPanel<T>({
  title,
  subtitle,
  columns,
  rows,
  getRowId,
  selectedId,
  onSelect,
  emptyMessage = 'No records available.',
}: NorthPanelProps<T>) {
  return (
    <section className="rounded-md border border-quantum-border bg-quantum-north">
      <div className="border-b border-quantum-border px-4 py-3">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">North Panel</div>
        <h2 className="mt-1 text-lg font-semibold text-slate-100">{title}</h2>
        {subtitle ? <p className="mt-1 text-sm text-slate-400">{subtitle}</p> : null}
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
            <tr>
              {columns.map((column) => (
                <th key={column.key} className={cn('px-4 py-3 font-medium', column.className)}>
                  {column.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.length > 0 ? (
              rows.map((row, index) => {
                const rowId = getRowId(row);
                const isSelected = selectedId === rowId;

                return (
                  <tr
                    key={rowId}
                    className={cn(
                      'border-t border-quantum-border transition',
                      index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-info',
                      onSelect && 'cursor-pointer hover:bg-slate-800/70',
                      isSelected && 'border-l-2 border-l-quantum-selected-border bg-quantum-selected',
                    )}
                    onClick={() => onSelect?.(row)}
                  >
                    {columns.map((column) => (
                      <td key={column.key} className={cn('px-4 py-3 align-top text-slate-200', column.className)}>
                        {column.render(row)}
                      </td>
                    ))}
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-slate-400">
                  {emptyMessage}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
