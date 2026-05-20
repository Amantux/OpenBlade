import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createSystemCertificate,
  deleteSystemCertificate,
  getBackupStatus,
  getEmailConfig,
  getSnmpConfig,
  getSnmpTraps,
  getSystemCertificates,
  getSystemConfig,
  getSystemDetail,
  getSystemOverview,
  getSystemSecurity,
  getSystemStatus,
  getSystemTime,
  getSystemUptime,
  getSystemVersion,
  testEmail,
  testSnmp,
  updateEmailConfig,
  updateSnmpConfig,
  updateSystemConfig,
  updateSystemSecurity,
  type CertificateCreateRequest,
  type CertificateSummaryResponse,
  type EmailConfigResponse,
  type SecurityConfigResponse,
  type SnmpConfigResponse,
  type SnmpTrapResponse,
} from '../api/system';
import type { SystemConfigResponse } from '../types/api';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import SystemDiagnostics from './SystemDiagnostics';
import SystemFirmware from './SystemFirmware';
import SystemNetwork from './SystemNetwork';
import { formatBytes, formatDate, formatDuration } from '../lib/utils';

const tabs = [
  { id: 'overview', label: 'Overview' },
  { id: 'network', label: 'Network' },
  { id: 'firmware', label: 'Firmware' },
  { id: 'diagnostics', label: 'Diagnostics' },
  { id: 'certificates', label: 'Certificates' },
  { id: 'snmp', label: 'SNMP' },
  { id: 'security', label: 'Security' },
  { id: 'email', label: 'Email' },
] as const;

type SystemTabId = (typeof tabs)[number]['id'];

const fieldClassName = 'mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-quantum-red';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'good':
    case 'healthy':
    case 'running':
    case 'up':
    case 'synced':
    case 'completed':
    case 'valid':
    case 'passed':
      return 'green';
    case 'warning':
    case 'uploaded':
    case 'manual':
      return 'amber';
    case 'failed':
    case 'critical':
    case 'down':
    case 'expired':
      return 'red';
    default:
      return 'gray';
  }
}

function Field({ label, children, helpText }: { label: string; children: React.ReactNode; helpText?: string }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="block text-xs uppercase tracking-[0.16em] text-slate-500">{label}</span>
      {children}
      {helpText ? <span className="mt-1 block text-xs text-slate-500">{helpText}</span> : null}
    </label>
  );
}

function ToggleField({
  label,
  checked,
  onChange,
  description,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
}) {
  return (
    <label className="flex items-start justify-between gap-4 rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200">
      <span>
        <span className="block font-medium text-slate-100">{label}</span>
        {description ? <span className="mt-1 block text-xs text-slate-500">{description}</span> : null}
      </span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="mt-1 rounded border border-quantum-border bg-quantum-panel" />
    </label>
  );
}

function OverviewTab() {
  const queryClient = useQueryClient();
  const overviewQuery = useQuery({ queryKey: ['system', 'overview'], queryFn: getSystemOverview, refetchInterval: 30_000 });
  const detailQuery = useQuery({ queryKey: ['system', 'detail'], queryFn: getSystemDetail, refetchInterval: 60_000 });
  const statusQuery = useQuery({ queryKey: ['system', 'status'], queryFn: getSystemStatus, refetchInterval: 15_000 });
  const versionQuery = useQuery({ queryKey: ['system', 'version'], queryFn: getSystemVersion, refetchInterval: 60_000 });
  const uptimeQuery = useQuery({ queryKey: ['system', 'uptime'], queryFn: getSystemUptime, refetchInterval: 15_000 });
  const timeQuery = useQuery({ queryKey: ['system', 'time'], queryFn: getSystemTime, refetchInterval: 15_000 });
  const backupQuery = useQuery({ queryKey: ['system', 'backup'], queryFn: getBackupStatus, refetchInterval: 60_000 });
  const configQuery = useQuery({ queryKey: ['system', 'config'], queryFn: getSystemConfig, refetchInterval: 60_000 });
  const [editMode, setEditMode] = useState(false);
  const [form, setForm] = useState<SystemConfigResponse>({
    hostname: '',
    timezone: 'UTC',
    locale: 'en_US',
    dateFormat: 'YYYY-MM-DD',
    temperatureUnit: 'celsius',
  });

  useEffect(() => {
    if (configQuery.data && !editMode) {
      setForm(configQuery.data);
    }
  }, [configQuery.data, editMode]);

  const saveMutation = useMutation({
    mutationFn: () => updateSystemConfig(form),
    onSuccess: async () => {
      setEditMode(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'config'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'overview'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'time'] }),
      ]);
    },
  });

  const resetForm = () => {
    if (configQuery.data) {
      setForm(configQuery.data);
    }
    setEditMode(false);
  };

  if ([overviewQuery, detailQuery, statusQuery, versionQuery, uptimeQuery, timeQuery, backupQuery, configQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [overviewQuery, detailQuery, statusQuery, versionQuery, uptimeQuery, timeQuery, backupQuery, configQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const overview = overviewQuery.data!;
  const detail = detailQuery.data!;
  const status = statusQuery.data!;
  const version = versionQuery.data!;
  const uptime = uptimeQuery.data!;
  const time = timeQuery.data!;
  const backup = backupQuery.data!;

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Overview</div>
            <h2 className="mt-1 text-2xl font-semibold text-slate-100">System identity</h2>
            <p className="mt-2 text-sm text-slate-400">Live system inventory, runtime status, backup posture, and clock state from AML system routes.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={statusVariant(status.overall)}>{status.overall}</Badge>
            {editMode ? (
              <>
                <Button type="button" variant="ghost" disabled={saveMutation.isPending} onClick={resetForm}>Cancel</Button>
                <Button type="button" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? 'Saving…' : 'Save Overview'}</Button>
              </>
            ) : (
              <Button type="button" variant="secondary" onClick={() => setEditMode(true)}>Edit Overview</Button>
            )}
          </div>
        </div>
        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-6">
          {[
            ['Hostname', overview.hostname],
            ['Version', `${version.software} / ${version.firmware}`],
            ['Serial', overview.serialNumber],
            ['Uptime', `${uptime.formatted} (${formatDuration(overview.uptime)})`],
            ['System Time', formatDate(time.local)],
            ['Backup', backup.status],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-4">
              <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</div>
              <div className="mt-2 text-sm font-medium text-slate-100">{value}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card>
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Configuration</div>
            <h3 className="mt-1 text-lg font-semibold text-slate-100">Editable system settings</h3>
          </div>
          <Badge variant="blue">/aml/system/config</Badge>
        </div>
        <div className="mt-4 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Field label="Hostname">
            <input className={fieldClassName} disabled={!editMode} value={form.hostname} onChange={(event) => setForm((current) => ({ ...current, hostname: event.target.value }))} />
          </Field>
          <Field label="Timezone">
            <input className={fieldClassName} disabled={!editMode} value={form.timezone} onChange={(event) => setForm((current) => ({ ...current, timezone: event.target.value }))} />
          </Field>
          <Field label="Locale">
            <input className={fieldClassName} disabled={!editMode} value={form.locale} onChange={(event) => setForm((current) => ({ ...current, locale: event.target.value }))} />
          </Field>
          <Field label="Date Format">
            <input className={fieldClassName} disabled={!editMode} value={form.dateFormat} onChange={(event) => setForm((current) => ({ ...current, dateFormat: event.target.value }))} />
          </Field>
          <Field label="Temperature Unit">
            <select className={fieldClassName} disabled={!editMode} value={form.temperatureUnit} onChange={(event) => setForm((current) => ({ ...current, temperatureUnit: event.target.value }))}>
              <option value="celsius">celsius</option>
              <option value="fahrenheit">fahrenheit</option>
            </select>
          </Field>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Hardware</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div className="flex items-center justify-between gap-3"><span>Model</span><span className="text-slate-100">{overview.model}</span></div>
            <div className="flex items-center justify-between gap-3"><span>CPU</span><span className="text-right text-slate-100">{detail.cpuModel}</span></div>
            <div className="flex items-center justify-between gap-3"><span>CPU Count</span><span className="text-slate-100">{detail.cpuCount}</span></div>
            <div className="flex items-center justify-between gap-3"><span>Memory</span><span className="text-slate-100">{detail.totalMem} GB</span></div>
            <div className="flex items-center justify-between gap-3"><span>Disk</span><span className="text-slate-100">{detail.totalDisk} GB</span></div>
            <div className="flex items-center justify-between gap-3"><span>Installed</span><span className="text-slate-100">{formatDate(detail.installedDate)}</span></div>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Subsystem health</div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {[
              ['CPU', status.cpu],
              ['Memory', status.memory],
              ['Disk', status.disk],
              ['Network', status.network],
              ['Services', status.services],
              ['Clock Sync', time.ntp ? 'NTP' : 'Manual'],
            ].map(([label, value]) => (
              <div key={label} className="rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-4">
                <div className="text-xs uppercase tracking-[0.16em] text-slate-500">{label}</div>
                <div className="mt-2 flex items-center justify-between gap-3">
                  <span className="text-sm text-slate-100">{value}</span>
                  <Badge variant={statusVariant(String(value))}>{value}</Badge>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Time & backup</div>
          <div className="mt-4 space-y-3 text-sm text-slate-300">
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">UTC</div>
              <div className="mt-1 text-slate-100">{formatDate(time.utc)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Local</div>
              <div className="mt-1 text-slate-100">{formatDate(time.local)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Timezone</div>
              <div className="mt-1 text-slate-100">{time.timezone}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Last Backup</div>
              <div className="mt-1 text-slate-100">{backup.lastBackup ? formatDate(backup.lastBackup) : 'No backup recorded'}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Backup Size</div>
              <div className="mt-1 text-slate-100">{formatBytes(backup.size)}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Location</div>
              <div className="mt-1 break-all text-slate-100">{backup.location ?? '—'}</div>
            </div>
          </div>
        </Card>
      </div>

      {saveMutation.isError ? <ErrorMessage error={saveMutation.error} /> : null}
    </div>
  );
}

function SecurityTab() {
  const queryClient = useQueryClient();
  const securityQuery = useQuery({ queryKey: ['system', 'security'], queryFn: getSystemSecurity });
  const [form, setForm] = useState<SecurityConfigResponse>({
    tlsEnabled: true,
    tlsVersion: 'TLS1.3',
    cipherSuites: [],
    certExpiry: '',
    sshEnabled: true,
    loginBanner: '',
  });

  useEffect(() => {
    if (securityQuery.data) {
      setForm(securityQuery.data);
    }
  }, [securityQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updateSystemSecurity(form),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'security'] });
    },
  });

  if (securityQuery.isLoading) {
    return <Spinner />;
  }
  if (securityQuery.isError) {
    return <ErrorMessage error={securityQuery.error} onRetry={() => void securityQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">TLS & Access</div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="space-y-4">
            <ToggleField label="TLS Enabled" checked={form.tlsEnabled} onChange={(checked) => setForm((current) => ({ ...current, tlsEnabled: checked }))} description="Controls HTTPS termination on the appliance." />
            <ToggleField label="SSH Enabled" checked={form.sshEnabled} onChange={(checked) => setForm((current) => ({ ...current, sshEnabled: checked }))} description="Mirrors the AML system security configuration." />
            <Field label="TLS Version"><input className={fieldClassName} value={form.tlsVersion} onChange={(event) => setForm((current) => ({ ...current, tlsVersion: event.target.value }))} /></Field>
            <Field label="Certificate Expiry"><input className={fieldClassName} value={form.certExpiry ?? ''} onChange={(event) => setForm((current) => ({ ...current, certExpiry: event.target.value }))} /></Field>
          </div>
          <div className="space-y-4">
            <Field label="Cipher Suites" helpText="Comma-separated cipher suite list.">
              <textarea className={`${fieldClassName} min-h-28`} value={form.cipherSuites.join(', ')} onChange={(event) => setForm((current) => ({ ...current, cipherSuites: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} />
            </Field>
            <Field label="Login Banner">
              <textarea className={`${fieldClassName} min-h-40`} value={form.loginBanner} onChange={(event) => setForm((current) => ({ ...current, loginBanner: event.target.value }))} />
            </Field>
          </div>
        </div>
        <div className="mt-4 flex justify-end"><Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? 'Saving…' : 'Save Security Settings'}</Button></div>
      </Card>
      {saveMutation.isError ? <ErrorMessage error={saveMutation.error} /> : null}
    </div>
  );
}

function CertificatesTab() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const certificatesQuery = useQuery({ queryKey: ['system', 'certificates'], queryFn: getSystemCertificates });
  const [form, setForm] = useState<CertificateCreateRequest>({ name: '', pem: '' });
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const resetForm = () => {
    setForm({ name: '', pem: '' });
    setFeedback(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const saveMutation = useMutation({
    mutationFn: (payload: CertificateCreateRequest) => createSystemCertificate(payload),
    onSuccess: async (summary) => {
      setFeedback({ type: 'success', message: summary });
      setForm({ name: '', pem: '' });
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      await queryClient.invalidateQueries({ queryKey: ['system', 'certificates'] });
    },
    onError: (error: unknown) => {
      setFeedback({
        type: 'error',
        message: error instanceof Error ? error.message : 'Unable to save certificate.',
      });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => deleteSystemCertificate(name),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'certificates'] });
    },
  });

  if (certificatesQuery.isLoading) {
    return <Spinner />;
  }
  if (certificatesQuery.isError) {
    return <ErrorMessage error={certificatesQuery.error} onRetry={() => void certificatesQuery.refetch()} />;
  }

  const certificates = certificatesQuery.data ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Certificates</div>
            <h2 className="mt-1 text-lg font-semibold text-slate-100">Installed certificates</h2>
          </div>
        </div>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Name</th>
                <th className="px-3 py-3">Subject</th>
                <th className="px-3 py-3">Expiry</th>
                <th className="px-3 py-3">Status</th>
                <th className="px-3 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {certificates.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-slate-400">No certificates are currently installed.</td>
                </tr>
              ) : (
                certificates.map((certificate: CertificateSummaryResponse) => (
                  <tr key={certificate.name} className="text-slate-200">
                    <td className="px-3 py-3 font-medium text-slate-100">{certificate.name}</td>
                    <td className="px-3 py-3">{certificate.subject}</td>
                    <td className="px-3 py-3">{certificate.expiry ? formatDate(certificate.expiry) : '—'}</td>
                    <td className="px-3 py-3"><Badge variant={statusVariant(certificate.status)}>{certificate.status}</Badge></td>
                    <td className="px-3 py-3">
                      <Button
                        type="button"
                        variant="danger"
                        disabled={deleteMutation.isPending}
                        onClick={() => deleteMutation.mutate(certificate.name)}
                      >
                        {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Add Certificate</div>
        <div className="mt-4 space-y-4">
          <Field label="Name">
            <input
              className={fieldClassName}
              value={form.name}
              onChange={(event) => {
                setFeedback(null);
                setForm((current) => ({ ...current, name: event.target.value }));
              }}
              placeholder="appliance-cert"
            />
          </Field>
          <div className="flex flex-wrap gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept=".pem,.crt,.cer"
              className="hidden"
              onChange={async (event) => {
                const file = event.target.files?.[0];
                if (!file) {
                  return;
                }
                const pem = await file.text();
                setFeedback(null);
                setForm((current) => ({
                  name: current.name || file.name.replace(/\.[^.]+$/, '') || 'uploaded-certificate',
                  pem,
                }));
              }}
            />
            <Button type="button" variant="secondary" onClick={() => fileInputRef.current?.click()}>
              Choose PEM File
            </Button>
            <span className="self-center text-sm text-slate-500">Or paste PEM content below.</span>
          </div>
          <Field label="PEM Content">
            <textarea
              className={`${fieldClassName} min-h-40`}
              value={form.pem}
              onChange={(event) => {
                setFeedback(null);
                setForm((current) => ({ ...current, pem: event.target.value }));
              }}
              placeholder="-----BEGIN CERTIFICATE-----"
            />
          </Field>
          {feedback ? (
            <div className={`rounded-md border px-4 py-3 text-sm ${feedback.type === 'success' ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200' : 'border-red-500/30 bg-red-950/20 text-red-200'}`}>
              {feedback.message}
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button type="button" variant="ghost" disabled={saveMutation.isPending} onClick={resetForm}>Cancel</Button>
            <Button
              type="button"
              disabled={saveMutation.isPending}
              onClick={() => {
                const name = form.name.trim();
                const pem = form.pem.trim();
                if (!name) {
                  setFeedback({ type: 'error', message: 'Certificate name is required.' });
                  return;
                }
                if (!pem) {
                  setFeedback({ type: 'error', message: 'PEM content is required.' });
                  return;
                }
                setFeedback(null);
                saveMutation.mutate({ name, pem });
              }}
            >
              {saveMutation.isPending ? 'Saving…' : 'Save'}
            </Button>
          </div>
        </div>
      </Card>

      {deleteMutation.isError ? <ErrorMessage error={deleteMutation.error} /> : null}
    </div>
  );
}

function SnmpTab() {
  const queryClient = useQueryClient();
  const snmpQuery = useQuery({ queryKey: ['system', 'snmp'], queryFn: getSnmpConfig });
  const trapsQuery = useQuery({ queryKey: ['system', 'snmp', 'traps'], queryFn: getSnmpTraps });
  const [form, setForm] = useState<SnmpConfigResponse>({ enabled: false, version: 'v2c', community: '', trapHosts: [], contact: '', location: '' });

  useEffect(() => {
    if (snmpQuery.data) {
      setForm(snmpQuery.data);
    }
  }, [snmpQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updateSnmpConfig(form),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'snmp'] });
    },
  });
  const testMutation = useMutation({
    mutationFn: testSnmp,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'snmp', 'traps'] });
    },
  });

  if ([snmpQuery, trapsQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }
  const errorQuery = [snmpQuery, trapsQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">SNMP</div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="space-y-4">
            <ToggleField label="SNMP Enabled" checked={form.enabled} onChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))} description="Enable SNMP polling and trap delivery." />
            <Field label="Version"><input className={fieldClassName} value={form.version} onChange={(event) => setForm((current) => ({ ...current, version: event.target.value }))} /></Field>
            <Field label="Community String"><input className={fieldClassName} value={form.community} onChange={(event) => setForm((current) => ({ ...current, community: event.target.value }))} /></Field>
            <Field label="Trap Hosts" helpText="Comma-separated trap targets.">
              <textarea className={`${fieldClassName} min-h-28`} value={form.trapHosts.join(', ')} onChange={(event) => setForm((current) => ({ ...current, trapHosts: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} />
            </Field>
            <Field label="Contact"><input className={fieldClassName} value={form.contact} onChange={(event) => setForm((current) => ({ ...current, contact: event.target.value }))} /></Field>
            <Field label="Location"><input className={fieldClassName} value={form.location} onChange={(event) => setForm((current) => ({ ...current, location: event.target.value }))} /></Field>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" disabled={testMutation.isPending} onClick={() => testMutation.mutate()}>{testMutation.isPending ? 'Sending…' : 'Send Test Trap'}</Button>
              <Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? 'Saving…' : 'Save SNMP'}</Button>
            </div>
          </div>
          <div>
            <div className="rounded-md border border-quantum-border bg-quantum-sidebar p-4">
              <div className="text-xs uppercase tracking-[0.16em] text-slate-500">Recent Traps</div>
              <div className="mt-3 space-y-3">
                {(trapsQuery.data ?? []).length === 0 ? (
                  <div className="text-sm text-slate-400">No SNMP traps recorded yet.</div>
                ) : (
                  (trapsQuery.data ?? []).map((trap: SnmpTrapResponse) => (
                    <div key={`${trap.timestamp}-${trap.oid}`} className="rounded-md border border-quantum-border bg-quantum-panel px-4 py-3 text-sm text-slate-300">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium text-slate-100">{trap.host}</span>
                        <span className="text-xs text-slate-500">{formatDate(trap.timestamp)}</span>
                      </div>
                      <div className="mt-1 text-xs text-slate-500">{trap.oid}</div>
                      <div className="mt-2">{trap.value}</div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </Card>
      {saveMutation.isError ? <ErrorMessage error={saveMutation.error} /> : null}
      {testMutation.isError ? <ErrorMessage error={testMutation.error} /> : null}
    </div>
  );
}

function EmailTab() {
  const queryClient = useQueryClient();
  const emailQuery = useQuery({ queryKey: ['system', 'email'], queryFn: getEmailConfig });
  const [form, setForm] = useState<EmailConfigResponse>({ enabled: false, smtpHost: '', smtpPort: 587, smtpUser: '', from: '', tls: true, recipients: [] });

  useEffect(() => {
    if (emailQuery.data) {
      setForm(emailQuery.data);
    }
  }, [emailQuery.data]);

  const saveMutation = useMutation({
    mutationFn: () => updateEmailConfig(form),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['system', 'email'] });
    },
  });
  const testMutation = useMutation({ mutationFn: testEmail });

  if (emailQuery.isLoading) {
    return <Spinner />;
  }
  if (emailQuery.isError) {
    return <ErrorMessage error={emailQuery.error} onRetry={() => void emailQuery.refetch()} />;
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Email</div>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="space-y-4">
            <ToggleField label="Email Alerts Enabled" checked={form.enabled} onChange={(checked) => setForm((current) => ({ ...current, enabled: checked }))} description="Send appliance notifications through SMTP." />
            <Field label="SMTP Host"><input className={fieldClassName} value={form.smtpHost} onChange={(event) => setForm((current) => ({ ...current, smtpHost: event.target.value }))} /></Field>
            <Field label="SMTP Port"><input type="number" className={fieldClassName} value={form.smtpPort} onChange={(event) => setForm((current) => ({ ...current, smtpPort: Number(event.target.value) || 0 }))} /></Field>
            <Field label="SMTP User"><input className={fieldClassName} value={form.smtpUser} onChange={(event) => setForm((current) => ({ ...current, smtpUser: event.target.value }))} /></Field>
          </div>
          <div className="space-y-4">
            <ToggleField label="Use TLS" checked={form.tls} onChange={(checked) => setForm((current) => ({ ...current, tls: checked }))} description="Negotiates STARTTLS for outbound email delivery." />
            <Field label="From Address"><input className={fieldClassName} value={form.from} onChange={(event) => setForm((current) => ({ ...current, from: event.target.value }))} /></Field>
            <Field label="Recipients" helpText="Comma-separated recipients for test and alert mail.">
              <textarea className={`${fieldClassName} min-h-28`} value={form.recipients.join(', ')} onChange={(event) => setForm((current) => ({ ...current, recipients: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} />
            </Field>
            <div className="flex flex-wrap justify-end gap-2">
              <Button variant="secondary" disabled={testMutation.isPending} onClick={() => testMutation.mutate()}>{testMutation.isPending ? 'Sending…' : 'Send Test Email'}</Button>
              <Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>{saveMutation.isPending ? 'Saving…' : 'Save Email'}</Button>
            </div>
          </div>
        </div>
      </Card>
      {saveMutation.isError ? <ErrorMessage error={saveMutation.error} /> : null}
      {testMutation.isError ? <ErrorMessage error={testMutation.error} /> : null}
    </div>
  );
}

export default function System() {
  const [activeTab, setActiveTab] = useState<SystemTabId>('overview');

  const activeContent = useMemo(() => {
    switch (activeTab) {
      case 'overview':
        return <OverviewTab />;
      case 'network':
        return <SystemNetwork />;
      case 'firmware':
        return <SystemFirmware />;
      case 'diagnostics':
        return <SystemDiagnostics />;
      case 'security':
        return <SecurityTab />;
      case 'certificates':
        return <CertificatesTab />;
      case 'snmp':
        return <SnmpTab />;
      case 'email':
        return <EmailTab />;
      default:
        return null;
    }
  }, [activeTab]);

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
        <h1 className="mt-1 text-2xl font-semibold text-slate-100">System Configuration</h1>
        <p className="mt-2 text-sm text-slate-400">Tabbed controls for overview, network, firmware, diagnostics, certificates, SNMP, security, and email services.</p>
        <div className="mt-4 flex flex-wrap gap-2">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={`rounded-md border px-4 py-2 text-sm font-medium transition ${
                activeTab === tab.id
                  ? 'border-quantum-red bg-quantum-red text-white'
                  : 'border-quantum-border bg-quantum-sidebar text-slate-300 hover:bg-quantum-panel hover:text-white'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </Card>
      {activeContent}
    </div>
  );
}
