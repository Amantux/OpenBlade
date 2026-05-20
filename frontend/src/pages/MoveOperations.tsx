import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createMove, listMoves, listPhysicalSlots } from '../api/operations';
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

function SlotTable({ slots, selected, onSelect }: { slots: PhysicalSlot[]; selected?: string; onSelect: (slot: PhysicalSlot) => void }) {
  return (
    <div className="overflow-x-auto rounded-md border border-quantum-border">
      <table className="min-w-full text-sm">
        <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
          <tr>
            <th className="px-4 py-3 font-medium">Slot</th>
            <th className="px-4 py-3 font-medium">Barcode</th>
            <th className="px-4 py-3 font-medium">State</th>
            <th className="px-4 py-3 font-medium">Select</th>
          </tr>
        </thead>
        <tbody>
          {slots.map((slot, index) => {
            const isSelected = selected === slot.address;
            return (
              <tr key={slot.address} className={isSelected ? 'bg-quantum-red/10' : index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
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
  );
}

export default function MoveOperations() {
  const queryClient = useQueryClient();
  const [step, setStep] = useState(1);
  const [source, setSource] = useState<PhysicalSlot>();
  const [destination, setDestination] = useState<PhysicalSlot>();
  const [successMessage, setSuccessMessage] = useState<string>();

  const slotsQuery = useQuery({ queryKey: ['operations', 'physical-slots'], queryFn: listPhysicalSlots });
  const movesQuery = useQuery({ queryKey: ['operations', 'moves'], queryFn: listMoves, refetchInterval: 10_000 });

  const sourceSlots = useMemo(
    () => (slotsQuery.data ?? []).filter((slot) => slot.elementType === 'slot' && slot.full && slot.barcode),
    [slotsQuery.data],
  );
  const destinationSlots = useMemo(
    () => (slotsQuery.data ?? []).filter((slot) => slot.elementType === 'slot' && !slot.full && slot.address !== source?.address),
    [slotsQuery.data, source?.address],
  );

  const moveMutation = useMutation({
    mutationFn: () => createMove(source?.address ?? '', destination?.address ?? '', source?.barcode ?? undefined),
    onSuccess: async () => {
      setSuccessMessage(`Move request submitted for ${source?.barcode ?? 'media'}.`);
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

  if (slotsQuery.isLoading || movesQuery.isLoading) {
    return <Spinner />;
  }
  if (slotsQuery.isError) {
    return <ErrorMessage error={slotsQuery.error} onRetry={() => slotsQuery.refetch()} />;
  }
  if (movesQuery.isError) {
    return <ErrorMessage error={movesQuery.error} onRetry={() => movesQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <p className="text-xs uppercase tracking-[0.22em] text-slate-500">Operations Center</p>
        <h1 className="mt-2 text-2xl font-semibold text-white">Move wizard</h1>
        <p className="mt-2 text-sm text-slate-400">Select a source cartridge, choose an empty destination slot, then submit the robotics move.</p>
      </Card>

      <div className="grid gap-3 lg:grid-cols-3">
        <StepChip step={1} current={step} label="Select source" />
        <StepChip step={2} current={step} label="Select destination" />
        <StepChip step={3} current={step} label="Confirm & execute" />
      </div>

      {successMessage ? (
        <Card className="border-emerald-500/30 bg-emerald-500/10 text-emerald-200">{successMessage}</Card>
      ) : null}
      {moveMutation.isError ? <ErrorMessage error={moveMutation.error} /> : null}

      {step === 1 ? (
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Step 1 · Source slot</h2>
              <p className="mt-1 text-sm text-slate-400">Choose an occupied slot to move.</p>
            </div>
            <div className="text-sm text-slate-400">{sourceSlots.length} occupied slots</div>
          </div>
          <SlotTable
            slots={sourceSlots}
            selected={source?.address}
            onSelect={(slot) => {
              setSource(slot);
              setDestination(undefined);
              setStep(2);
            }}
          />
        </Card>
      ) : null}

      {step === 2 ? (
        <Card>
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">Step 2 · Destination slot</h2>
              <p className="mt-1 text-sm text-slate-400">Choose an empty slot for {source?.barcode ?? 'the selected cartridge'}.</p>
            </div>
            <Button variant="ghost" onClick={() => setStep(1)}>Back</Button>
          </div>
          <SlotTable
            slots={destinationSlots}
            selected={destination?.address}
            onSelect={(slot) => {
              setDestination(slot);
              setStep(3);
            }}
          />
        </Card>
      ) : null}

      {step === 3 ? (
        <Card>
          <h2 className="text-lg font-semibold text-white">Step 3 · Confirm</h2>
          <div className="mt-4 rounded-md border border-quantum-border bg-quantum-panel p-4 text-slate-300">
            Moving <span className="font-semibold text-white">{source?.barcode}</span> from Slot <span className="font-mono text-white">{source?.address}</span> → Slot <span className="font-mono text-white">{destination?.address}</span>
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <Button variant="ghost" onClick={() => setStep(2)}>Back</Button>
            <Button disabled={!source || !destination || moveMutation.isPending} onClick={() => moveMutation.mutate()}>
              {moveMutation.isPending ? 'Submitting…' : 'Submit move'}
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
                <th className="px-4 py-3 font-medium">Move ID</th>
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
