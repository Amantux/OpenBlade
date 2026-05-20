import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createCacheDrive,
  deleteCacheDrive,
  listCacheDrives,
  updateCacheDrive,
  type CacheDriveConfig,
  type CacheDriveInput,
  type EvictionPolicy,
} from '../../api/nas';
import BytesDisplay from '../../components/nas/BytesDisplay';
import ConfirmDialog from '../../components/nas/ConfirmDialog';
import NasStatusBadge from '../../components/nas/NasStatusBadge';
import Button from '../../components/ui/Button';
import Card from '../../components/ui/Card';
import ErrorMessage from '../../components/ui/ErrorMessage';
import Spinner from '../../components/ui/Spinner';
import { cn, formatBytes, toTitleCase } from '../../lib/utils';

const DEFAULT_CACHE_DRIVE_KEY = 'openblade.nas.default-cache-drive';
const evictionPolicies: EvictionPolicy[] = ['never', 'after_verified', 'after_days', 'lru', 'manual'];

interface ToastState {
  type: 'success' | 'error';
  message: string;
}

const emptyForm: CacheDriveInput = {
  name: '',
  root_path: '',
  max_bytes: 12_000_000_000_000,
  min_free_bytes: 1_000_000_000_000,
  eviction_policy: 'after_verified',
  retention_days: 30,
  verify_before_archive: true,
  verify_after_archive: true,
  quarantine_failed_files: true,
};

function Field({ label, children, helpText }: { label: string; children: React.ReactNode; helpText?: string }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">{label}</span>
      {children}
      {helpText ? <span className="mt-1 block text-xs text-slate-500">{helpText}</span> : null}
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

function getConfiguredUsage(drive: CacheDriveConfig): { usedBytes: number; ratio: number } {
  const usedBytes = Math.max(drive.max_bytes - drive.min_free_bytes, 0);
  const ratio = drive.max_bytes > 0 ? usedBytes / drive.max_bytes : 0;
  return { usedBytes, ratio };
}

function getHealthStatus(drive: CacheDriveConfig): { label: string; value: string } {
  if (!drive.enabled) {
    return { label: 'Disabled', value: 'disabled' };
  }
  const { ratio } = getConfiguredUsage(drive);
  if (ratio >= 0.9) {
    return { label: 'Near capacity', value: 'critical' };
  }
  if (ratio >= 0.75) {
    return { label: 'Watch', value: 'warning' };
  }
  return { label: 'Healthy', value: 'healthy' };
}

function CacheDriveModal({
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
  form: CacheDriveInput;
  isSaving: boolean;
  onChange: (updater: (current: CacheDriveInput) => CacheDriveInput) => void;
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
            <div className="text-xs uppercase tracking-[0.2em] text-slate-500">Storage cache tier</div>
            <h2 className="mt-2 text-2xl font-semibold text-slate-100">{title}</h2>
          </div>
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>Close</Button>
        </div>

        <div className="mt-6 grid gap-4 md:grid-cols-2">
          <Field label="Name">
            <input value={form.name} onChange={(event) => onChange((current) => ({ ...current, name: event.target.value }))} required />
          </Field>
          <Field label="Root path">
            <input value={form.root_path} onChange={(event) => onChange((current) => ({ ...current, root_path: event.target.value }))} required />
          </Field>
          <Field label="Max bytes">
            <input type="number" min={1} value={form.max_bytes} onChange={(event) => onChange((current) => ({ ...current, max_bytes: Number(event.target.value) || 0 }))} />
          </Field>
          <Field label="Min free bytes">
            <input type="number" min={0} value={form.min_free_bytes} onChange={(event) => onChange((current) => ({ ...current, min_free_bytes: Number(event.target.value) || 0 }))} />
          </Field>
          <Field label="Eviction policy">
            <select value={form.eviction_policy} onChange={(event) => onChange((current) => ({ ...current, eviction_policy: event.target.value as EvictionPolicy }))}>
              {evictionPolicies.map((value) => (
                <option key={value} value={value}>{toTitleCase(value)}</option>
              ))}
            </select>
          </Field>
          {form.eviction_policy === 'after_days' ? (
            <Field label="Retention days">
              <input type="number" min={0} value={form.retention_days} onChange={(event) => onChange((current) => ({ ...current, retention_days: Number(event.target.value) || 0 }))} />
            </Field>
          ) : (
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar p-4 text-sm text-slate-400">
              Retention days are only used with the <strong>After Days</strong> eviction policy.
            </div>
          )}
          <ToggleField label="Verify before archive" checked={form.verify_before_archive} onChange={(checked) => onChange((current) => ({ ...current, verify_before_archive: checked }))} />
          <ToggleField label="Verify after archive" checked={form.verify_after_archive} onChange={(checked) => onChange((current) => ({ ...current, verify_after_archive: checked }))} />
          <ToggleField label="Quarantine failed files" checked={form.quarantine_failed_files} onChange={(checked) => onChange((current) => ({ ...current, quarantine_failed_files: checked }))} />
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <Button type="button" variant="ghost" disabled={isSaving} onClick={onClose}>Cancel</Button>
          <Button type="button" disabled={isSaving} onClick={onSubmit}>{isSaving ? 'Saving…' : 'Save cache drive'}</Button>
        </div>
      </div>
    </div>
  );
}

export default function CacheDrives() {
  const queryClient = useQueryClient();
  const cacheDrivesQuery = useQuery({ queryKey: ['nas', 'cache-drives'], queryFn: listCacheDrives });
  const [form, setForm] = useState<CacheDriveInput>(emptyForm);
  const [editingDrive, setEditingDrive] = useState<CacheDriveConfig | null>(null);
  const [driveToDelete, setDriveToDelete] = useState<CacheDriveConfig | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [selectedDriveId, setSelectedDriveId] = useState<string | null>(null);
  const [defaultDriveId, setDefaultDriveId] = useState<string | null>(() =>
    typeof window === 'undefined' ? null : window.localStorage.getItem(DEFAULT_CACHE_DRIVE_KEY),
  );
  const [toast, setToast] = useState<ToastState | null>(null);

  useEffect(() => {
    if (!toast) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const drives = useMemo(() => cacheDrivesQuery.data ?? [], [cacheDrivesQuery.data]);
  const selectedDrive = drives.find((drive) => drive.id === selectedDriveId) ?? drives[0] ?? null;

  useEffect(() => {
    if (!selectedDriveId && drives[0]) {
      setSelectedDriveId(drives[0].id);
    }
  }, [drives, selectedDriveId]);

  const saveMutation = useMutation({
    mutationFn: (payload: CacheDriveInput) => (editingDrive ? updateCacheDrive(editingDrive.id, payload) : createCacheDrive(payload)),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ['nas', 'cache-drives'] });
      setSelectedDriveId(saved.id);
      setToast({ type: 'success', message: editingDrive ? 'Cache drive updated.' : 'Cache drive created.' });
      setEditingDrive(null);
      setForm(emptyForm);
      setIsModalOpen(false);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to save cache drive.' });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (driveId: string) => deleteCacheDrive(driveId),
    onSuccess: async () => {
      if (driveToDelete && defaultDriveId === driveToDelete.id) {
        window.localStorage.removeItem(DEFAULT_CACHE_DRIVE_KEY);
        setDefaultDriveId(null);
      }
      await queryClient.invalidateQueries({ queryKey: ['nas', 'cache-drives'] });
      setToast({ type: 'success', message: 'Cache drive deleted.' });
      setDriveToDelete(null);
    },
    onError: (error) => {
      setToast({ type: 'error', message: error instanceof Error ? error.message : 'Unable to delete cache drive.' });
    },
  });

  function openNewDrive() {
    setEditingDrive(null);
    setForm(emptyForm);
    setIsModalOpen(true);
  }

  function openEditDrive(drive: CacheDriveConfig) {
    setEditingDrive(drive);
    setForm({
      id: drive.id,
      name: drive.name,
      root_path: drive.root_path,
      max_bytes: drive.max_bytes,
      min_free_bytes: drive.min_free_bytes,
      eviction_policy: drive.eviction_policy,
      retention_days: drive.retention_days,
      verify_before_archive: drive.verify_before_archive,
      verify_after_archive: drive.verify_after_archive,
      quarantine_failed_files: drive.quarantine_failed_files,
      quarantine_path: drive.quarantine_path,
      allow_source_delete_after_verify: drive.allow_source_delete_after_verify,
      stabilization_seconds: drive.stabilization_seconds,
      support_reflink_or_hardlink: drive.support_reflink_or_hardlink,
      enabled: drive.enabled,
    });
    setIsModalOpen(true);
  }

  function submitForm() {
    if (!form.name.trim() || !form.root_path.trim()) {
      setToast({ type: 'error', message: 'Name and root path are required.' });
      return;
    }
    if (form.max_bytes <= 0) {
      setToast({ type: 'error', message: 'Max bytes must be greater than zero.' });
      return;
    }
    saveMutation.mutate(form);
  }

  function runAction(label: string) {
    if (!selectedDrive) {
      setToast({ type: 'error', message: 'Select a cache drive first.' });
      return;
    }
    setToast({ type: 'success', message: `${label} queued for ${selectedDrive.name}.` });
  }

  function setAsDefault() {
    if (!selectedDrive) {
      setToast({ type: 'error', message: 'Select a cache drive first.' });
      return;
    }
    window.localStorage.setItem(DEFAULT_CACHE_DRIVE_KEY, selectedDrive.id);
    setDefaultDriveId(selectedDrive.id);
    setToast({ type: 'success', message: `${selectedDrive.name} set as the default cache drive.` });
  }

  if (cacheDrivesQuery.isLoading) {
    return <Spinner />;
  }

  if (cacheDrivesQuery.isError) {
    return <ErrorMessage error={cacheDrivesQuery.error} onRetry={() => void cacheDrivesQuery.refetch()} />;
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
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Cache Drives</h1>
            <p className="mt-2 text-sm text-slate-400">Configure ingest cache targets, eviction behavior, and verification controls for cache-drive mode.</p>
          </div>
          <Button type="button" onClick={openNewDrive}>Add Cache Drive</Button>
        </div>
      </Card>

      <Card>
        <div className="flex flex-wrap items-center gap-2 border-b border-quantum-border pb-4">
          <Button type="button" variant="secondary" onClick={() => runAction('Test write/read')}>Test Write/Read</Button>
          <Button type="button" variant="secondary" onClick={() => runAction('Evict verified files')}>Evict Verified Files</Button>
          <Button type="button" variant="secondary" onClick={setAsDefault}>Set as Default</Button>
          {selectedDrive ? <div className="ml-auto text-sm text-slate-400">Selected: <span className="text-slate-100">{selectedDrive.name}</span></div> : null}
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Name</th>
                <th className="px-3 py-3">Root Path</th>
                <th className="px-3 py-3">Max Bytes</th>
                <th className="px-3 py-3">Eviction Policy</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Usage</th>
                <th className="px-3 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {drives.map((drive) => {
                const health = getHealthStatus(drive);
                const usage = getConfiguredUsage(drive);
                const isSelected = selectedDrive?.id === drive.id;
                return (
                  <tr key={drive.id} className={cn('cursor-pointer text-slate-200 transition', isSelected && 'bg-quantum-selected/20')} onClick={() => setSelectedDriveId(drive.id)}>
                    <td className="px-3 py-3">
                      <div className="font-medium text-slate-100">{drive.name}</div>
                      <div className="mt-1 flex flex-wrap gap-2 text-xs">
                        <span className="text-slate-500">{drive.id}</span>
                        {defaultDriveId === drive.id ? <NasStatusBadge value="healthy" label="Default" /> : null}
                      </div>
                    </td>
                    <td className="px-3 py-3 font-mono text-xs text-slate-300">{drive.root_path}</td>
                    <td className="px-3 py-3"><BytesDisplay value={drive.max_bytes} /></td>
                    <td className="px-3 py-3">{toTitleCase(drive.eviction_policy)}</td>
                    <td className="px-3 py-3"><NasStatusBadge value={health.value} label={health.label} /></td>
                    <td className="px-3 py-3">
                      <div className="space-y-2">
                        <div className="text-slate-100">{formatBytes(usage.usedBytes)} / {formatBytes(drive.max_bytes)}</div>
                        <div className="h-2 w-40 rounded-full bg-quantum-sidebar">
                          <div className={cn('h-2 rounded-full', usage.ratio >= 0.9 ? 'bg-red-400' : usage.ratio >= 0.75 ? 'bg-amber-400' : 'bg-emerald-400')} style={{ width: `${Math.min(100, Math.round(usage.ratio * 100))}%` }} />
                        </div>
                      </div>
                    </td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button type="button" variant="secondary" onClick={(event) => { event.stopPropagation(); openEditDrive(drive); }}>Edit</Button>
                        <Button type="button" variant="danger" onClick={(event) => { event.stopPropagation(); setDriveToDelete(drive); }}>Delete</Button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {drives.length === 0 ? (
          <div className="rounded-md border border-dashed border-quantum-border px-4 py-8 text-center text-sm text-slate-400">
            No cache drives configured yet.
          </div>
        ) : null}
      </Card>

      <CacheDriveModal
        open={isModalOpen}
        title={editingDrive ? 'Edit Cache Drive' : 'Add Cache Drive'}
        form={form}
        isSaving={saveMutation.isPending}
        onChange={(updater) => setForm((current) => updater(current))}
        onClose={() => {
          setEditingDrive(null);
          setForm(emptyForm);
          setIsModalOpen(false);
        }}
        onSubmit={submitForm}
      />

      <ConfirmDialog
        open={Boolean(driveToDelete)}
        title="Delete cache drive"
        message={driveToDelete ? `Delete ${driveToDelete.name}? This cannot be undone.` : ''}
        confirmLabel="Delete"
        isProcessing={deleteMutation.isPending}
        onCancel={() => setDriveToDelete(null)}
        onConfirm={() => {
          if (driveToDelete) {
            deleteMutation.mutate(driveToDelete.id);
          }
        }}
      />
    </div>
  );
}
