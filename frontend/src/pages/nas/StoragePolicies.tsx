import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createPolicy,
  deletePolicy,
  listPolicies,
  updatePolicy,
  type IngestMode,
  type PolicyInput,
  type PolicyType,
  type ShardStrategy,
  type StoragePolicy,
} from '../../api/nas';
import ConfirmDialog from '../../components/nas/ConfirmDialog';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { toTitleCase } from '../../lib/utils';

const policyTypes: PolicyType[] = ['critical_sequential', 'noncritical_sharded', 'balanced'];
const ingestModes: IngestMode[] = ['cache_drive', 'source_stream'];
const shardStrategies: ShardStrategy[] = ['round_robin', 'capacity_weighted', 'directory_batch', 'hash_prefix', 'restore_parallelism_optimized'];

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

const emptyForm: PolicyInput = {
  name: '',
  policy_type: 'balanced',
  default_ingest_mode: 'cache_drive',
  copies_required: 1,
  verify_before_archive: true,
  verify_after_archive: true,
  allow_spillover: true,
  allow_sharding: false,
  max_parallelism: 1,
  shard_strategy: 'round_robin',
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function ToggleField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex items-center justify-between gap-4 rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

function PolicyModal({
  open,
  title,
  form,
  isSaving,
  onChange,
  onClose,
  onSubmit,
}: {
  open: boolean;
  title: string;
  form: PolicyInput;
  isSaving: boolean;
  onChange: (updater: (current: PolicyInput) => PolicyInput) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/70 px-4 py-8">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-lg border border-quantum-border bg-quantum-info p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">NAS storage policies</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">{title}</h2>
          </div>
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>
            Close
          </Button>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <input value={form.name} onChange={(event) => onChange((current) => ({ ...current, name: event.target.value }))} required />
          </Field>
          <Field label="Policy type">
            <select value={form.policy_type} onChange={(event) => onChange((current) => ({ ...current, policy_type: event.target.value as PolicyType }))}>
              {policyTypes.map((value) => (
                <option key={value} value={value}>{toTitleCase(value)}</option>
              ))}
            </select>
          </Field>
          <Field label="Default ingest mode">
            <select value={form.default_ingest_mode} onChange={(event) => onChange((current) => ({ ...current, default_ingest_mode: event.target.value as IngestMode }))}>
              {ingestModes.map((value) => (
                <option key={value} value={value}>{toTitleCase(value)}</option>
              ))}
            </select>
          </Field>
          <Field label="Copies required">
            <input type="number" min={1} max={4} value={form.copies_required} onChange={(event) => onChange((current) => ({ ...current, copies_required: Number(event.target.value) || 1 }))} />
          </Field>
          <Field label="Max parallelism">
            <input type="number" min={1} max={16} value={form.max_parallelism} onChange={(event) => onChange((current) => ({ ...current, max_parallelism: Number(event.target.value) || 1 }))} />
          </Field>
          <div className="rounded-md border border-quantum-border bg-quantum-sidebar p-4 text-sm text-slate-400 md:col-span-2">
            Tune verification, spillover, and sharding behaviors to match the ingest risk profile for each workload.
          </div>
          <ToggleField label="Verify before archive" checked={form.verify_before_archive} onChange={(checked) => onChange((current) => ({ ...current, verify_before_archive: checked }))} />
          <ToggleField label="Verify after archive" checked={form.verify_after_archive} onChange={(checked) => onChange((current) => ({ ...current, verify_after_archive: checked }))} />
          <ToggleField label="Allow spillover" checked={form.allow_spillover} onChange={(checked) => onChange((current) => ({ ...current, allow_spillover: checked }))} />
          <ToggleField label="Allow sharding" checked={form.allow_sharding} onChange={(checked) => onChange((current) => ({ ...current, allow_sharding: checked, shard_strategy: checked ? current.shard_strategy ?? 'round_robin' : null }))} />
          {form.allow_sharding ? (
            <div className="md:col-span-2">
              <Field label="Shard strategy">
                <select value={form.shard_strategy ?? 'round_robin'} onChange={(event) => onChange((current) => ({ ...current, shard_strategy: event.target.value as ShardStrategy }))}>
                  {shardStrategies.map((value) => (
                    <option key={value} value={value}>{toTitleCase(value)}</option>
                  ))}
                </select>
              </Field>
            </div>
          ) : null}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>Cancel</Button>
          <Button type="button" disabled={isSaving} onClick={onSubmit}>{isSaving ? 'Saving…' : 'Save policy'}</Button>
        </div>
      </div>
    </div>
  );
}

export default function StoragePolicies() {
  const queryClient = useQueryClient();
  const policiesQuery = useQuery({ queryKey: ['nas', 'policies'], queryFn: listPolicies });
  const [form, setForm] = useState<PolicyInput>(emptyForm);
  const [editingPolicy, setEditingPolicy] = useState<StoragePolicy | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [policyToDelete, setPolicyToDelete] = useState<StoragePolicy | null>(null);
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }

    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const saveMutation = useMutation({
    mutationFn: (payload: PolicyInput) => (editingPolicy ? updatePolicy(editingPolicy.id, payload) : createPolicy(payload)),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'policies'] });
      setToast({ type: 'success', message: editingPolicy ? 'Storage policy updated.' : 'Storage policy created.' });
      setIsModalOpen(false);
      setEditingPolicy(null);
      setForm(emptyForm);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to save storage policy.' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (policyId: string) => deletePolicy(policyId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'policies'] });
      setToast({ type: 'success', message: 'Storage policy deleted.' });
      setPolicyToDelete(null);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to delete storage policy.' });
    },
  });

  const policies = useMemo(() => policiesQuery.data ?? [], [policiesQuery.data]);

  function openNewPolicy() {
    setEditingPolicy(null);
    setForm(emptyForm);
    setIsModalOpen(true);
  }

  function openEditPolicy(policy: StoragePolicy) {
    setEditingPolicy(policy);
    setForm({
      id: policy.id,
      name: policy.name,
      policy_type: policy.policy_type,
      default_ingest_mode: policy.default_ingest_mode,
      copies_required: policy.copies_required,
      verify_before_archive: policy.verify_before_archive,
      verify_after_archive: policy.verify_after_archive,
      allow_spillover: policy.allow_spillover,
      allow_sharding: policy.allow_sharding,
      max_parallelism: policy.max_parallelism,
      shard_strategy: policy.shard_strategy,
      manifest_strategy: policy.manifest_strategy,
      cache_retention: policy.cache_retention,
      allow_source_delete: policy.allow_source_delete,
    });
    setIsModalOpen(true);
  }

  function submitForm() {
    if (!form.name.trim()) {
      setToast({ type: 'error', message: 'Policy name is required.' });
      return;
    }
    saveMutation.mutate({
      ...form,
      copies_required: Math.min(4, Math.max(1, form.copies_required)),
      max_parallelism: Math.min(16, Math.max(1, form.max_parallelism)),
      shard_strategy: form.allow_sharding ? form.shard_strategy ?? 'round_robin' : null,
    });
  }

  if (policiesQuery.isLoading) {
    return <Spinner />;
  }

  if (policiesQuery.isError) {
    return <ErrorMessage error={policiesQuery.error} onRetry={() => void policiesQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      {toast ? (
        <div className={`fixed right-4 top-4 z-50 rounded-md border px-4 py-3 text-sm shadow-lg ${toast.type === 'success' ? 'border-emerald-500/30 bg-emerald-900/90 text-emerald-100' : 'border-red-500/30 bg-red-950/90 text-red-100'}`}>
          {toast.message}
        </div>
      ) : null}

      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Storage</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Storage Policies</h1>
            <p className="mt-2 text-sm text-slate-400">Manage ingest defaults, verification requirements, spillover safety, and sharding strategy for NAS archive jobs.</p>
          </div>
          <Button type="button" onClick={openNewPolicy}>New Policy</Button>
        </div>
      </Card>

      <Card>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Name</th>
                <th className="px-3 py-3">Type</th>
                <th className="px-3 py-3">Ingest Mode</th>
                <th className="px-3 py-3">Copies</th>
                <th className="px-3 py-3">Verify Before</th>
                <th className="px-3 py-3">Verify After</th>
                <th className="px-3 py-3">Allow Sharding</th>
                <th className="px-3 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {policies.map((policy) => (
                <tr key={policy.id} className="text-slate-200">
                  <td className="px-3 py-3">
                    <div className="font-medium text-slate-100">{policy.name}</div>
                    <div className="mt-1 text-xs text-slate-500">{policy.id}</div>
                  </td>
                  <td className="px-3 py-3"><NasStatusBadge value={policy.policy_type} /></td>
                  <td className="px-3 py-3"><NasStatusBadge value={policy.default_ingest_mode} /></td>
                  <td className="px-3 py-3">{policy.copies_required}</td>
                  <td className="px-3 py-3">{policy.verify_before_archive ? 'Yes' : 'No'}</td>
                  <td className="px-3 py-3">{policy.verify_after_archive ? 'Yes' : 'No'}</td>
                  <td className="px-3 py-3">{policy.allow_sharding ? toTitleCase(policy.shard_strategy ?? 'enabled') : 'No'}</td>
                  <td className="px-3 py-3">
                    <div className="flex flex-wrap gap-2">
                      <Button type="button" variant="secondary" onClick={() => openEditPolicy(policy)}>Edit</Button>
                      <Button type="button" variant="danger" onClick={() => setPolicyToDelete(policy)}>Delete</Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {policies.length === 0 ? (
          <div className="rounded-md border border-dashed border-quantum-border px-4 py-8 text-center text-sm text-slate-400">
            No storage policies found.
          </div>
        ) : null}
      </Card>

      <PolicyModal
        open={isModalOpen}
        title={editingPolicy ? 'Edit Storage Policy' : 'New Storage Policy'}
        form={form}
        isSaving={saveMutation.isPending}
        onChange={(updater) => setForm((current) => updater(current))}
        onClose={() => {
          setIsModalOpen(false);
          setEditingPolicy(null);
          setForm(emptyForm);
        }}
        onSubmit={submitForm}
      />

      <ConfirmDialog
        open={Boolean(policyToDelete)}
        title="Delete storage policy"
        message={policyToDelete ? `Delete ${policyToDelete.name}? This cannot be undone.` : ''}
        confirmLabel="Delete"
        isProcessing={deleteMutation.isPending}
        onCancel={() => setPolicyToDelete(null)}
        onConfirm={() => {
          if (policyToDelete) {
            deleteMutation.mutate(policyToDelete.id);
          }
        }}
      />
    </div>
  );
}
