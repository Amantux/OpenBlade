import Button from '../ui/Button';

interface JsonViewerModalProps {
  open: boolean;
  title: string;
  data: unknown;
  onClose: () => void;
}

export default function JsonViewerModal({ open, title, data, onClose }: JsonViewerModalProps) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 px-4 py-8">
      <div className="flex max-h-[90vh] w-full max-w-4xl flex-col rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">JSON viewer</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">{title}</h2>
          </div>
          <Button type="button" variant="ghost" onClick={onClose}>
            Close
          </Button>
        </div>

        <pre className="mt-6 overflow-auto rounded-md border border-quantum-border bg-quantum-sidebar p-4 text-sm text-slate-100">
          <code>{JSON.stringify(data, null, 2)}</code>
        </pre>
      </div>
    </div>
  );
}
