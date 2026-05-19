import { RotateCcw } from 'lucide-react';
import { ApiError } from '../../api/client';
import Button from './Button';
import Card from './Card';

interface ErrorMessageProps {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}

export default function ErrorMessage({ error, onRetry, title }: ErrorMessageProps) {
  const apiError = error instanceof ApiError ? error : null;
  const heading = title ?? apiError?.message ?? 'Unable to load data';
  const impact = apiError?.impact ?? 'Live appliance information is temporarily unavailable.';
  const action = apiError?.action ?? 'Retry in a moment or verify the backend is reachable.';
  const details = apiError?.details ?? (error instanceof Error ? error.message : String(error));

  return (
    <Card className="border-red-500/20 bg-red-950/20">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-red-200">{heading}</h3>
          <p className="text-sm text-red-100/80">Impact: {impact}</p>
          <p className="text-sm text-red-100/80">Action: {action}</p>
          <details className="rounded-lg border border-red-500/20 bg-black/10 p-3 text-xs text-red-100/70">
            <summary className="cursor-pointer font-medium">Details</summary>
            <pre className="mt-2 whitespace-pre-wrap font-mono">{details}</pre>
          </details>
        </div>
        {onRetry ? (
          <Button variant="secondary" onClick={onRetry} className="gap-2 self-start">
            <RotateCcw className="h-4 w-4" />
            Retry
          </Button>
        ) : null}
      </div>
    </Card>
  );
}
