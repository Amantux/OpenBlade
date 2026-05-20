import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getDnsConfig,
  getNetworkInterfaces,
  getNetworkRoutes,
  getNtpConfig,
  updateDnsConfig,
  updateNetworkInterface,
  updateNtpConfig,
  type DnsConfigResponse,
  type NetworkInterfaceResponse,
  type NtpConfigResponse,
} from '../api/system';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { formatDate } from '../lib/utils';

const fieldClassName = 'mt-2 w-full rounded-md border border-quantum-border bg-quantum-sidebar px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-quantum-red disabled:cursor-not-allowed disabled:opacity-60';

function statusVariant(status: string): 'gray' | 'green' | 'blue' | 'amber' | 'red' | 'redDim' {
  switch (status.toLowerCase()) {
    case 'up':
    case 'enabled':
    case 'synced':
      return 'green';
    case 'warning':
    case 'manual':
      return 'amber';
    case 'down':
    case 'failed':
      return 'red';
    default:
      return 'gray';
  }
}

function areStringArraysEqual(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

export default function SystemNetwork() {
  const queryClient = useQueryClient();
  const interfacesQuery = useQuery({ queryKey: ['system', 'network', 'interfaces'], queryFn: getNetworkInterfaces, refetchInterval: 30_000 });
  const dnsQuery = useQuery({ queryKey: ['system', 'network', 'dns'], queryFn: getDnsConfig, refetchInterval: 60_000 });
  const ntpQuery = useQuery({ queryKey: ['system', 'network', 'ntp'], queryFn: getNtpConfig, refetchInterval: 60_000 });
  const routesQuery = useQuery({ queryKey: ['system', 'network', 'routes'], queryFn: getNetworkRoutes, refetchInterval: 60_000 });
  const [editMode, setEditMode] = useState(false);
  const [interfaceEdits, setInterfaceEdits] = useState<Record<string, Pick<NetworkInterfaceResponse, 'ip' | 'mask' | 'gateway' | 'duplex'>>>({});
  const [dnsForm, setDnsForm] = useState<DnsConfigResponse>({ primary: '', secondary: '', search: [], domain: '' });
  const [ntpForm, setNtpForm] = useState<NtpConfigResponse>({ enabled: false, servers: [], status: 'unknown', lastSync: null });
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    if (interfacesQuery.data && !editMode) {
      setInterfaceEdits(
        Object.fromEntries(
          interfacesQuery.data.map((item) => [
            item.name,
            { ip: item.ip, mask: item.mask, gateway: item.gateway, duplex: item.duplex },
          ]),
        ),
      );
    }
  }, [editMode, interfacesQuery.data]);

  useEffect(() => {
    if (dnsQuery.data && !editMode) {
      setDnsForm(dnsQuery.data);
    }
  }, [dnsQuery.data, editMode]);

  useEffect(() => {
    if (ntpQuery.data && !editMode) {
      setNtpForm(ntpQuery.data);
    }
  }, [editMode, ntpQuery.data]);

  useEffect(() => {
    if (!saveSuccess) {
      return undefined;
    }

    const timer = window.setTimeout(() => setSaveSuccess(false), 2_000);
    return () => window.clearTimeout(timer);
  }, [saveSuccess]);

  const resetForms = () => {
    if (interfacesQuery.data) {
      setInterfaceEdits(
        Object.fromEntries(
          interfacesQuery.data.map((item) => [
            item.name,
            { ip: item.ip, mask: item.mask, gateway: item.gateway, duplex: item.duplex },
          ]),
        ),
      );
    }
    if (dnsQuery.data) {
      setDnsForm(dnsQuery.data);
    }
    if (ntpQuery.data) {
      setNtpForm(ntpQuery.data);
    }
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const interfaceUpdates = (interfacesQuery.data ?? []).filter((item) => {
        const draft = interfaceEdits[item.name];
        return draft !== undefined && (
          draft.ip !== item.ip ||
          draft.mask !== item.mask ||
          draft.gateway !== item.gateway ||
          draft.duplex !== item.duplex
        );
      });

      const dnsChanged = dnsQuery.data !== undefined && (
        dnsForm.primary !== dnsQuery.data.primary ||
        dnsForm.secondary !== dnsQuery.data.secondary ||
        dnsForm.domain !== dnsQuery.data.domain ||
        !areStringArraysEqual(dnsForm.search, dnsQuery.data.search)
      );

      const ntpChanged = ntpQuery.data !== undefined && (
        ntpForm.enabled !== ntpQuery.data.enabled ||
        !areStringArraysEqual(ntpForm.servers, ntpQuery.data.servers)
      );

      if (!interfaceUpdates.length && !dnsChanged && !ntpChanged) {
        return;
      }

      await Promise.all([
        ...interfaceUpdates.map((item) => updateNetworkInterface(item.name, interfaceEdits[item.name])),
        ...(dnsChanged ? [updateDnsConfig({ ...dnsForm, search: dnsForm.search.filter(Boolean) })] : []),
        ...(ntpChanged ? [updateNtpConfig({ ...ntpForm, servers: ntpForm.servers.filter(Boolean) })] : []),
      ]);
    },
    onSuccess: async () => {
      setEditMode(false);
      setSaveSuccess(true);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['system', 'network', 'interfaces'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'network', 'dns'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'network', 'ntp'] }),
        queryClient.invalidateQueries({ queryKey: ['system', 'network', 'routes'] }),
      ]);
    },
  });

  if ([interfacesQuery, dnsQuery, ntpQuery, routesQuery].some((query) => query.isLoading)) {
    return <Spinner />;
  }

  const errorQuery = [interfacesQuery, dnsQuery, ntpQuery, routesQuery].find((query) => query.isError);
  if (errorQuery) {
    return <ErrorMessage error={errorQuery.error} onRetry={() => void errorQuery.refetch()} />;
  }

  const interfaces = interfacesQuery.data ?? [];
  const routes = routesQuery.data ?? [];

  return (
    <div className="space-y-4">
      <Card className="bg-quantum-north">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">System</div>
            <h1 className="mt-1 text-2xl font-semibold text-slate-100">Network</h1>
            <p className="mt-2 text-sm text-slate-400">Detailed interface inventory, DNS, NTP, and route visibility backed by AML network routes.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {editMode ? (
              <>
                <Button
                  type="button"
                  variant="ghost"
                  disabled={saveMutation.isPending}
                  onClick={() => {
                    setSaveSuccess(false);
                    resetForms();
                    setEditMode(false);
                  }}
                >
                  Cancel
                </Button>
                <Button type="button" disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                  {saveMutation.isPending ? 'Saving…' : 'Save Network'}
                </Button>
              </>
            ) : (
              <>
                {saveSuccess ? <span className="self-center text-sm font-medium text-emerald-300">Saved ✓</span> : null}
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => {
                    setSaveSuccess(false);
                    setEditMode(true);
                  }}
                >
                  Edit Network
                </Button>
              </>
            )}
          </div>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {interfaces.map((item) => (
          <Card key={item.name}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.18em] text-slate-500">{item.type}</div>
                <h2 className="mt-1 text-lg font-semibold text-slate-100">{item.name}</h2>
              </div>
              <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
            </div>
            <div className="mt-4 space-y-3 text-sm text-slate-300">
              <label className="block">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">IP</span>
                <input
                  className={fieldClassName}
                  disabled={!editMode}
                  value={interfaceEdits[item.name]?.ip ?? ''}
                  onChange={(event) => setInterfaceEdits((current) => ({ ...current, [item.name]: { ...current[item.name], ip: event.target.value } }))}
                />
              </label>
              <label className="block">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Mask</span>
                <input
                  className={fieldClassName}
                  disabled={!editMode}
                  value={interfaceEdits[item.name]?.mask ?? ''}
                  onChange={(event) => setInterfaceEdits((current) => ({ ...current, [item.name]: { ...current[item.name], mask: event.target.value } }))}
                />
              </label>
              <label className="block">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Gateway</span>
                <input
                  className={fieldClassName}
                  disabled={!editMode}
                  value={interfaceEdits[item.name]?.gateway ?? ''}
                  onChange={(event) => setInterfaceEdits((current) => ({ ...current, [item.name]: { ...current[item.name], gateway: event.target.value } }))}
                />
              </label>
              <div className="flex items-center justify-between gap-3"><span>MAC</span><span className="text-slate-100">{item.mac}</span></div>
              <div className="flex items-center justify-between gap-3"><span>Speed</span><span className="text-slate-100">{item.speed}</span></div>
              <label className="block">
                <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Duplex</span>
                <input
                  className={fieldClassName}
                  disabled={!editMode}
                  value={interfaceEdits[item.name]?.duplex ?? ''}
                  onChange={(event) => setInterfaceEdits((current) => ({ ...current, [item.name]: { ...current[item.name], duplex: event.target.value } }))}
                />
              </label>
            </div>
          </Card>
        ))}
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">DNS Configuration</div>
          <div className="mt-4 space-y-4 text-sm text-slate-300">
            <label className="block">
              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Primary</span>
              <input className={fieldClassName} disabled={!editMode} value={dnsForm.primary} onChange={(event) => setDnsForm((current) => ({ ...current, primary: event.target.value }))} />
            </label>
            <label className="block">
              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Secondary</span>
              <input className={fieldClassName} disabled={!editMode} value={dnsForm.secondary} onChange={(event) => setDnsForm((current) => ({ ...current, secondary: event.target.value }))} />
            </label>
            <label className="block">
              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Search Domains</span>
              <input className={fieldClassName} disabled={!editMode} value={dnsForm.search.join(', ')} onChange={(event) => setDnsForm((current) => ({ ...current, search: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} />
            </label>
            <label className="block">
              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Domain</span>
              <input className={fieldClassName} disabled={!editMode} value={dnsForm.domain} onChange={(event) => setDnsForm((current) => ({ ...current, domain: event.target.value }))} />
            </label>
          </div>
        </Card>

        <Card>
          <div className="text-xs uppercase tracking-[0.18em] text-slate-500">NTP Configuration</div>
          <div className="mt-4 space-y-4 text-sm text-slate-300">
            <label className="flex items-center justify-between gap-3 rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3">
              <span>
                <span className="block font-medium text-slate-100">Enable NTP</span>
                <span className="mt-1 block text-xs text-slate-500">Current sync status: {ntpForm.status}</span>
              </span>
              <input type="checkbox" className="rounded border border-quantum-border bg-quantum-panel" disabled={!editMode} checked={ntpForm.enabled} onChange={(event) => setNtpForm((current) => ({ ...current, enabled: event.target.checked }))} />
            </label>
            <label className="block">
              <span className="text-xs uppercase tracking-[0.16em] text-slate-500">Servers</span>
              <input className={fieldClassName} disabled={!editMode} value={ntpForm.servers.join(', ')} onChange={(event) => setNtpForm((current) => ({ ...current, servers: event.target.value.split(',').map((item) => item.trim()).filter(Boolean) }))} />
            </label>
            <div className="flex items-center justify-between gap-3"><span>Last Sync</span><span className="text-slate-100">{ntpForm.lastSync ? formatDate(ntpForm.lastSync) : 'Never'}</span></div>
          </div>
        </Card>
      </div>

      <Card>
        <div className="text-xs uppercase tracking-[0.18em] text-slate-500">Routes</div>
        <div className="mt-4 overflow-x-auto">
          <table className="min-w-full divide-y divide-quantum-border text-sm">
            <thead className="text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-3 py-3">Destination</th>
                <th className="px-3 py-3">Mask</th>
                <th className="px-3 py-3">Gateway</th>
                <th className="px-3 py-3">Interface</th>
                <th className="px-3 py-3">Metric</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-quantum-border/80">
              {routes.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-3 py-8 text-center text-slate-400">No static routes are configured.</td>
                </tr>
              ) : (
                routes.map((route) => (
                  <tr key={`${route.destination}-${route.gateway}`} className="text-slate-200">
                    <td className="px-3 py-3">{route.destination}</td>
                    <td className="px-3 py-3">{route.mask}</td>
                    <td className="px-3 py-3">{route.gateway}</td>
                    <td className="px-3 py-3">{route.interface}</td>
                    <td className="px-3 py-3">{route.metric}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {saveMutation.isError ? <ErrorMessage error={saveMutation.error} /> : null}
    </div>
  );
}
