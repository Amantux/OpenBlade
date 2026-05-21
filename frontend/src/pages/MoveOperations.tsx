import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createMove, listActiveJobs, listMoves, listPhysicalSlots } from '../api/operations';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import type { PhysicalSlot } from '../types/api';
import { formatDate } from '../lib/utils';

function StepChip({ step, current, label }: { step: number; current: number; label: string }) {
  const active = current === step;
  const complete = current > step;
  return (
    <div className={`flex items-center gap-3 rounded-md border px-4 py-3 ${complete ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300' : active ? 'border-quantum-red bg-quantum-red/10 text-white' : 'border-quantum-border bg-quantum-panel text-slate-400'}`}>
      <span className="flex h-7 w-7 items-center justify-center rounded-full border text-xs font-semibold">{step}</span>
      <span className="text-sm font-semibold">{label}</span>
    </div>
  );
}

function elementLabel(slot: PhysicalSlot): string {
  const normalized = String(slot.elementType).toUpperCase();
  if (normalized === 'IE' || normalized === 'IESTATION') return 'IE';
  if (normalized === 'DRIVE') return 'Drive';
  return 'Slot';
}

function CoordinateTable({
  slots,
  selected,
  title,
  subtitle,
  onSelect,
}: {
  slots: PhysicalSlot[];
  selected?: string;
  title: string;
  subtitle: string;
  onSelect: (slot: PhysicalSlot) => void;
}) {
  return (
    <Card>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-white">{title}</h2>
          <p className="mt-1 text-sm text-slate-400">{subtitle}</p>
        </div>
        <div className="text-sm text-slate-400">{slots.length} candidate{slots.length === 1 ? '' : 's'}</div>
      </div>
      <div className="overflow-x-auto rounded-md border border-quantum-border">
        <table className="min-w-full text-sm">
          <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
            <tr>
              <th className="px-4 py-3">Type</th>
              <th className="px-4 py-3">Address</th>
              <th className="px-4 py-3">Barcode</th>
              <th className="px-4 py-3">State</th>
              <th className="px-4 py-3">Select</th>
            </tr>
          </thead>
          <tbody>
            {slots.map((slot, index) => {
              const isSelected = selected === slot.address;
              return (
                <tr key={slot.address} className={isSelected ? 'bg-quantum-red/10' : index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 text-slate-300">{elementLabel(slot)}</td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-200">{slot.address}</td>
                  <td className="px-4 py-3 text-slate-300">{slot.barcode ?? 'Empty'}</td>
                  <td className="px-4 py-3 text-slate-300">{slot.state}</td>
                  <td className="px-4 py-3">
                    <Button variant={isSelected ? 'primary' : 'secondary'} onClick={() => onSelect(slot)}>
                      {isSelected ? 'Selected' : 'Choose'}
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export default function MoveOperations() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(1);
  const [source, setSource] = useState<PhysicalSlot>();
  const [destination, setDestination] = useState<PhysicalSlot>();
  const [submittedJobId, setSubmittedJobId] = useState<string>('');

  const slotsQuery = useQuery({ queryKey: ['operations', 'physical-slots'], queryFn: listPhysicalSlots, refetchInterval: 15_000 });
  const movesQuery = useQuery({ queryKey: ['operations', 'moves'], queryFn: listMoves, refetchInterval: 10_000 });
  const jobsQuery = useQuery({ queryKey: ['operations', 'jobs', 'active'], queryFn: listActiveJobs, refetchInterval: 5_000 });

  const sourceCandidates = useMemo(
    () => (slotsQuery.data ?? []).filter((slot) => slot.full && slot.barcode),
    [slotsQuery.data],
  );
  const destinationCandidates = useMemo(
    () => (slotsQuery.data ?? []).filter((slot) => !slot.full && slot.address !== source?.address),
    [slotsQuery.data, source?.address],
  );

  const moveMutation = useMutation({
    mutationFn: () => createMove(source?.address ?? '', destination?.address ?? '', source?.barcode ?? undefined),
    onSuccess: async (receipt) => {
      setSubmittedJobId(receipt.jobId);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['operations', 'physical-slots'] }),
        queryClient.invalidateQueries({ queryKey: ['operations', 'moves'] }),
        queryClient.invalidateQueries({ queryKey: ['operations', 'jobs', 'active'] }),
      ]);
      setStep(1);
      setSource(undefined);
      setDestination(undefined);
    },
  });

  if (slotsQuery.isLoading || movesQuery.isLoading || jobsQuery.isLoading) {
    return <Spinner />;
  }
  if (slotsQuery.isError) {
    return <ErrorMessage error={slotsQuery.error} onRetry={() => void slotsQuery.refetch()} />;
  }
  if (movesQuery.isError) {
    return <ErrorMessage error={movesQuery.error} onRetry={() => void movesQuery.refetch()} />;
  }
  if (jobsQuery.isError) {
    return <ErrorMessage error={jobsQuery.error} onRetry={() => void jobsQuery.refetch()} />;
  }

  const previewValid = Boolean(source && destination && !destination.full && source.address !== destination.address);
  const submittedJob = (jobsQuery.data ?? []).find((job) => job.id === submittedJobId) ?? (movesQuery.data ?? []).find((move) => move.id === submittedJobId);

  return (
    <div className="space-y-4">
      <Card>
        <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Operations Center</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Move wizard</h1>
        <p className="mt-2 text-sm text-slate-400">
          Select a source coordinate, choose an empty destination coordinate, review the dry-run preview,
          and queue a move job without calling direct hardware move APIs.
        </p>
      </Card>

      <div className="grid gap-3 lg:grid-cols-3">
        <StepChip step={1} current={step} label="Select source" />
        <StepChip step={2} current={step} label="Select destination" />
        <StepChip step={3} current={step} label="Preview & queue" />
      </div>

      {submittedJobId ? (
        <Card className="border-emerald-500/30 bg-emerald-500/10 text-emerald-200">
          Move job queued as <Link className="font-mono underline" to="/jobs">{submittedJobId}</Link>
          {submittedJob && 'state' in submittedJob ? ` · status ${(submittedJob as { state: string }).state}` : ''}
        </Card>
      ) : null}
      {moveMutation.isError ? <ErrorMessage error={moveMutation.error} /> : null}

      {step === 1 ? (
        <CoordinateTable
          slots={sourceCandidates}
          selected={source?.address}
          title="Step 1 · Source coordinate"
          subtitle="Occupied slots, drives, or IE cells that currently hold media."
          onSelect={(slot) => {
            setSource(slot);
            setDestination(undefined);
            setStep(2);
          }}
        />
      ) : null}

      {step === 2 ? (
        <div className="space-y-4">
          <div className="flex justify-end"><Button variant="ghost" onClick={() => setStep(1)}>Back</Button></div>
          <CoordinateTable
            slots={destinationCandidates}
            selected={destination?.address}
            title="Step 2 · Destination coordinate"
            subtitle="Only empty coordinates are offered so the destination is validated before confirmation."
            onSelect={(slot) => {
              setDestination(slot);
              setStep(3);
            }}
          />
        </div>
      ) : null}

      {step === 3 ? (
        <Card>
          <h2 className="text-lg font-semibold text-white">Step 3 · Dry-run preview</h2>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <div className="rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-300">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Source</div>
              <div className="mt-2 text-slate-100">{source?.barcode}</div>
              <div className="mt-1 font-mono text-xs">{source?.address}</div>
              <div className="mt-1">{elementLabel(source!)}</div>
            </div>
            <div className="rounded-md border border-quantum-border bg-quantum-panel p-4 text-sm text-slate-300">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Destination</div>
              <div className="mt-2 text-slate-100">{destination?.barcode ?? 'Empty target'}</div>
              <div className="mt-1 font-mono text-xs">{destination?.address}</div>
              <div className="mt-1">{elementLabel(destination!)}</div>
            </div>
          </div>
          <div className="mt-4 rounded-md border border-amber-500/30 bg-amber-900/10 px-4 py-3 text-sm text-amber-200">
            Preview only: the destination has been validated as empty and the action will be queued as a job.
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button variant="ghost" onClick={() => setStep(2)}>Back</Button>
            <Button disabled={!previewValid || moveMutation.isPending} onClick={() => moveMutation.mutate()}>
              {moveMutation.isPending ? 'Queuing…' : 'Queue Move Job'}
            </Button>
          </div>
        </Card>
      ) : null}

      <Card>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-white">Recent move requests</h2>
          <Button variant="secondary" onClick={() => void movesQuery.refetch()}>Refresh</Button>
        </div>
        <div className="overflow-x-auto rounded-md border border-quantum-border">
          <table className="min-w-full text-sm">
            <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
              <tr>
                <th className="px-4 py-3 font-medium">Job ID</th>
                <th className="px-4 py-3 font-medium">Barcode</th>
                <th className="px-4 py-3 font-medium">Source</th>
                <th className="px-4 py-3 font-medium">Destination</th>
                <th className="px-4 py-3 font-medium">State</th>
                <th className="px-4 py-3 font-medium">Started</th>
              </tr>
            </thead>
            <tbody>
              {(movesQuery.data ?? []).map((move, index) => (
                <tr key={move.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                  <td className="px-4 py-3 font-mono text-xs text-slate-200">{move.id}</td>
                  <td className="px-4 py-3 text-slate-300">{move.barcode}</td>
                  <td className="px-4 py-3 text-slate-300">{move.source}</td>
                  <td className="px-4 py-3 text-slate-300">{move.destination}</td>
                  <td className="px-4 py-3 text-slate-300">{move.state}</td>
                  <td className="px-4 py-3 text-slate-300">{formatDate(move.startedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
