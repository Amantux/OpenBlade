import Button from '../ui/Button';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmVariant?: 'primary' | 'danger';
  isProcessing?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  confirmVariant = 'danger',
  isProcessing = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4">
      <div className="w-full max-w-md rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Confirm action</div>
        <h2 className="mt-2 text-xl font-semibold text-slate-100">{title}</h2>
        <p className="mt-3 text-sm text-slate-400">{message}</p>
        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={isProcessing} onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button type="button" variant={confirmVariant} disabled={isProcessing} onClick={onConfirm}>
            {isProcessing ? 'Working…' : confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
