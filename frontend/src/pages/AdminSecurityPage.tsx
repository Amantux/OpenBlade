import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Badge from '../components/ui/Badge';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { ALL_RBAC_PERMISSIONS, createAdminRole, createAdminToken, createAdminUser, deleteAdminUser, listAdminAuditEvents, listAdminRoles, listAdminTokens, listAdminUsers, revokeAdminToken, type AdminAuditEvent, type AdminRole, type RbacPermission } from '../api/admin';
import { formatDate, toTitleCase } from '../lib/utils';

interface NoticeState {
  type: 'success' | 'error';
  message: string;
}

interface UserFormState {
  username: string;
  password: string;
  role_id: string;
  is_admin: boolean;
}

interface TokenFormState {
  name: string;
  expires_at: string;
  permissions: RbacPermission[];
}

interface RoleFormState {
  id: string;
  name: string;
  description: string;
  permissions: RbacPermission[];
}

const defaultUserForm: UserFormState = {
  username: '',
  password: '',
  role_id: '',
  is_admin: false,
};

const defaultTokenForm: TokenFormState = {
  name: '',
  expires_at: '',
  permissions: ['token:manage'],
};

const defaultRoleForm: RoleFormState = {
  id: '',
  name: '',
  description: '',
  permissions: [],
};

function roleVariant(roleId: string): 'red' | 'blue' | 'gray' {
  if (roleId === 'admin') {
    return 'red';
  }
  if (roleId === 'operator') {
    return 'blue';
  }
  return 'gray';
}

function truncateDetails(event: AdminAuditEvent): string {
  const raw = JSON.stringify(event.details);
  if (raw.length <= 72) {
    return raw;
  }
  return `${raw.slice(0, 72)}…`;
}

function togglePermission<T extends string>(permissions: T[], permission: T): T[] {
  return permissions.includes(permission)
    ? permissions.filter((value) => value !== permission)
    : [...permissions, permission];
}

export default function AdminSecurityPage() {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<NoticeState | null>(null);
  const [rawToken, setRawToken] = useState<string>('');
  const [userForm, setUserForm] = useState<UserFormState>(defaultUserForm);
  const [tokenForm, setTokenForm] = useState<TokenFormState>(defaultTokenForm);
  const [roleForm, setRoleForm] = useState<RoleFormState>(defaultRoleForm);

  const usersQuery = useQuery({ queryKey: ['admin', 'users'], queryFn: listAdminUsers, refetchInterval: 30_000 });
  const rolesQuery = useQuery({ queryKey: ['admin', 'roles'], queryFn: listAdminRoles, refetchInterval: 30_000 });
  const tokensQuery = useQuery({ queryKey: ['admin', 'tokens'], queryFn: listAdminTokens, refetchInterval: 30_000 });
  const auditQuery = useQuery({ queryKey: ['admin', 'audit'], queryFn: () => listAdminAuditEvents(50), refetchInterval: 30_000 });

  useEffect(() => {
    if (!notice) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    if (!rawToken) {
      return undefined;
    }
    const timeout = window.setTimeout(() => setRawToken(''), 15_000);
    return () => window.clearTimeout(timeout);
  }, [rawToken]);

  useEffect(() => {
    const roles = rolesQuery.data ?? [];
    if (!roles.length) {
      return;
    }
    setUserForm((current) => {
      if (current.role_id) {
        return current;
      }
      return { ...current, role_id: roles[0].id };
    });
  }, [rolesQuery.data]);

  const createUserMutation = useMutation({
    mutationFn: () =>
      createAdminUser({
        username: userForm.username.trim(),
        password: userForm.password,
        role_id: userForm.role_id,
        is_admin: userForm.is_admin,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
      setUserForm((current) => ({ ...defaultUserForm, role_id: current.role_id }));
      setNotice({ type: 'success', message: 'User created.' });
    },
    onError: (error) => {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : 'Unable to create user.' });
    },
  });

  const deleteUserMutation = useMutation({
    mutationFn: (userId: string) => deleteAdminUser(userId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
      setNotice({ type: 'success', message: 'User deleted.' });
    },
    onError: (error) => {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : 'Unable to delete user.' });
    },
  });

  const createTokenMutation = useMutation({
    mutationFn: () =>
      createAdminToken({
        name: tokenForm.name.trim(),
        permissions: tokenForm.permissions,
        expires_at: tokenForm.expires_at || undefined,
      }),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'tokens'] });
      setRawToken(result.raw_token);
      setTokenForm(defaultTokenForm);
      setNotice({ type: 'success', message: 'Token created. Copy the raw token now.' });
    },
    onError: (error) => {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : 'Unable to create token.' });
    },
  });

  const revokeTokenMutation = useMutation({
    mutationFn: (tokenId: string) => revokeAdminToken(tokenId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'tokens'] });
      setNotice({ type: 'success', message: 'Token revoked.' });
    },
    onError: (error) => {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : 'Unable to revoke token.' });
    },
  });

  const createRoleMutation = useMutation({
    mutationFn: () =>
      createAdminRole({
        id: roleForm.id.trim(),
        name: roleForm.name.trim(),
        description: roleForm.description.trim(),
        permissions: roleForm.permissions,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'roles'] });
      setRoleForm(defaultRoleForm);
      setNotice({ type: 'success', message: 'Role created.' });
    },
    onError: (error) => {
      setNotice({ type: 'error', message: error instanceof Error ? error.message : 'Unable to create role.' });
    },
  });

  const queryError = usersQuery.error ?? rolesQuery.error ?? tokensQuery.error ?? auditQuery.error;
  const userMap = useMemo(
    () => new Map((usersQuery.data ?? []).map((user) => [user.id, user.username])),
    [usersQuery.data],
  );
  const roleOptions = rolesQuery.data ?? [];

  function handleAdminToggle(checked: boolean) {
    setUserForm((current) => {
      const adminRole = roleOptions.find((role) => role.id === 'admin')?.id;
      const nonAdminRole = roleOptions.find((role) => role.id !== 'admin')?.id ?? roleOptions[0]?.id ?? '';
      return {
        ...current,
        is_admin: checked,
        role_id: checked ? adminRole ?? current.role_id : current.role_id === 'admin' ? nonAdminRole : current.role_id,
      };
    });
  }

  function refreshAll() {
    void Promise.all([
      usersQuery.refetch(),
      rolesQuery.refetch(),
      tokensQuery.refetch(),
      auditQuery.refetch(),
    ]);
  }

  if ((usersQuery.isLoading || rolesQuery.isLoading || tokensQuery.isLoading || auditQuery.isLoading) && !queryError) {
    return <Spinner />;
  }

  if (queryError) {
    return <ErrorMessage error={queryError} onRetry={refreshAll} />;
  }

  const users = usersQuery.data ?? [];
  const roles = rolesQuery.data ?? [];
  const tokens = tokensQuery.data ?? [];
  const auditEvents = auditQuery.data ?? [];

  return (
    <div className="space-y-4">
      {notice ? (
        <div className={`rounded-md border px-4 py-3 text-sm ${notice.type === 'success' ? 'border-emerald-500/30 bg-emerald-900/30 text-emerald-100' : 'border-red-500/30 bg-red-950/30 text-red-100'}`}>
          {notice.message}
        </div>
      ) : null}

      {rawToken ? (
        <div className="rounded-md border border-amber-500/30 bg-amber-900/20 px-4 py-3 text-sm text-amber-100">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-semibold">Raw API token</div>
              <div className="mt-1 break-all font-mono text-xs">{rawToken}</div>
            </div>
            <Button variant="ghost" onClick={() => setRawToken('')}>Hide</Button>
          </div>
        </div>
      ) : null}

      <Card className="bg-quantum-info">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Admin</div>
            <h1 className="mt-1 text-2xl font-semibold text-white">Security</h1>
            <p className="mt-2 text-sm text-slate-400">Manage RBAC users, roles, API tokens, and audit visibility from the admin console.</p>
          </div>
          <Button variant="secondary" onClick={refreshAll}>Refresh</Button>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.1fr,0.9fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">Users</h2>
              <p className="mt-1 text-sm text-slate-400">Create and deactivate authenticated console users.</p>
            </div>
            <Badge variant="blue">{users.length}</Badge>
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-4">
            <label className="block text-sm text-slate-300 md:col-span-2">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Username</span>
              <input value={userForm.username} onChange={(event) => setUserForm((current) => ({ ...current, username: event.target.value }))} />
            </label>
            <label className="block text-sm text-slate-300 md:col-span-2">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Password</span>
              <input type="password" value={userForm.password} onChange={(event) => setUserForm((current) => ({ ...current, password: event.target.value }))} />
            </label>
            <label className="block text-sm text-slate-300 md:col-span-2">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Role</span>
              <select value={userForm.role_id} onChange={(event) => setUserForm((current) => ({ ...current, role_id: event.target.value }))}>
                <option value="">Select a role</option>
                {roles.map((role) => (
                  <option key={role.id} value={role.id}>{role.name}</option>
                ))}
              </select>
            </label>
            <label className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-sidebar px-4 py-3 text-sm text-slate-200 md:col-span-2">
              <span>Administrator access</span>
              <input type="checkbox" checked={userForm.is_admin} onChange={(event) => handleAdminToggle(event.target.checked)} />
            </label>
          </div>

          <div className="mt-4 flex justify-end">
            <Button disabled={!userForm.username.trim() || !userForm.password || !userForm.role_id || createUserMutation.isPending} onClick={() => createUserMutation.mutate()}>
              {createUserMutation.isPending ? 'Creating…' : 'Create User'}
            </Button>
          </div>

          {users.length === 0 ? (
            <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
              No users returned by /aml/auth/users.
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Username</th>
                    <th className="px-4 py-3 font-medium">Is Admin</th>
                    <th className="px-4 py-3 font-medium">Created At</th>
                    <th className="px-4 py-3 font-medium">Last Login</th>
                    <th className="px-4 py-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user, index) => (
                    <tr key={user.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                      <td className="px-4 py-3">
                        <div className="font-medium text-white">{user.username}</div>
                        <div className="mt-1 text-xs text-slate-500">{user.role_id}</div>
                      </td>
                      <td className="px-4 py-3"><Badge variant={user.is_admin ? 'red' : roleVariant(user.role_id)}>{user.is_admin ? 'Yes' : 'No'}</Badge></td>
                      <td className="px-4 py-3 text-slate-300">{formatDate(user.created_at)}</td>
                      <td className="px-4 py-3 text-slate-300">{formatDate(user.last_login_at ?? '')}</td>
                      <td className="px-4 py-3 text-right">
                        <Button
                          variant="danger"
                          disabled={deleteUserMutation.isPending}
                          onClick={() => {
                            if (window.confirm(`Delete ${user.username}?`)) {
                              deleteUserMutation.mutate(user.id);
                            }
                          }}
                        >
                          Delete
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <h2 className="text-lg font-semibold text-white">Role Management</h2>
          <p className="mt-1 text-sm text-slate-400">Create reusable RBAC roles for operators and service accounts.</p>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Role ID</span>
              <input value={roleForm.id} onChange={(event) => setRoleForm((current) => ({ ...current, id: event.target.value }))} placeholder="archive-supervisor" />
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Role Name</span>
              <input value={roleForm.name} onChange={(event) => setRoleForm((current) => ({ ...current, name: event.target.value }))} placeholder="Archive Supervisor" />
            </label>
            <label className="block text-sm text-slate-300 md:col-span-2">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Description</span>
              <input value={roleForm.description} onChange={(event) => setRoleForm((current) => ({ ...current, description: event.target.value }))} />
            </label>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-2">
            {ALL_RBAC_PERMISSIONS.map((permission) => (
              <label key={permission} className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
                <span>{permission}</span>
                <input
                  type="checkbox"
                  checked={roleForm.permissions.includes(permission)}
                  onChange={() => setRoleForm((current) => ({ ...current, permissions: togglePermission(current.permissions, permission) }))}
                />
              </label>
            ))}
          </div>

          <div className="mt-4 flex justify-end">
            <Button disabled={!roleForm.id.trim() || !roleForm.name.trim() || createRoleMutation.isPending} onClick={() => createRoleMutation.mutate()}>
              {createRoleMutation.isPending ? 'Creating…' : 'Create Role'}
            </Button>
          </div>

          <div className="mt-4 space-y-3">
            {roles.map((role: AdminRole) => (
              <div key={role.id} className="rounded-md border border-quantum-border bg-quantum-panel p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="font-semibold text-white">{role.name}</div>
                    <div className="mt-1 text-xs text-slate-500">{role.id}</div>
                  </div>
                  <Badge variant={roleVariant(role.id)}>{role.permissions.length} permission(s)</Badge>
                </div>
                <div className="mt-3 text-sm text-slate-300">{role.description || 'No description provided.'}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.05fr,0.95fr]">
        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">API Tokens</h2>
              <p className="mt-1 text-sm text-slate-400">Create personal tokens and revoke them when they are no longer needed.</p>
            </div>
            <Badge variant="blue">{tokens.length}</Badge>
          </div>

          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Token Name</span>
              <input value={tokenForm.name} onChange={(event) => setTokenForm((current) => ({ ...current, name: event.target.value }))} placeholder="cli" />
            </label>
            <label className="block text-sm text-slate-300">
              <span className="mb-2 block text-xs uppercase tracking-[0.16em] text-slate-500">Expires At</span>
              <input type="datetime-local" value={tokenForm.expires_at} onChange={(event) => setTokenForm((current) => ({ ...current, expires_at: event.target.value }))} />
            </label>
          </div>

          <div className="mt-4 grid gap-2 md:grid-cols-2">
            {ALL_RBAC_PERMISSIONS.map((permission) => (
              <label key={permission} className="flex items-center justify-between rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-200">
                <span>{permission}</span>
                <input
                  type="checkbox"
                  checked={tokenForm.permissions.includes(permission)}
                  onChange={() => setTokenForm((current) => ({ ...current, permissions: togglePermission(current.permissions, permission) }))}
                />
              </label>
            ))}
          </div>

          <div className="mt-4 flex justify-end">
            <Button disabled={!tokenForm.name.trim() || tokenForm.permissions.length === 0 || createTokenMutation.isPending} onClick={() => createTokenMutation.mutate()}>
              {createTokenMutation.isPending ? 'Creating…' : 'Create Token'}
            </Button>
          </div>

          {tokens.length === 0 ? (
            <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
              No API tokens returned by /aml/auth/tokens.
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Token ID</th>
                    <th className="px-4 py-3 font-medium">Created By</th>
                    <th className="px-4 py-3 font-medium">Created At</th>
                    <th className="px-4 py-3 font-medium">Last Used</th>
                    <th className="px-4 py-3 font-medium">Expires At</th>
                    <th className="px-4 py-3 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map((token, index) => (
                    <tr key={token.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                      <td className="px-4 py-3 font-mono text-xs text-slate-200">{token.id}</td>
                      <td className="px-4 py-3 text-slate-300">{userMap.get(token.user_id) ?? token.user_id}</td>
                      <td className="px-4 py-3 text-slate-300">{formatDate(token.created_at)}</td>
                      <td className="px-4 py-3 text-slate-300">{formatDate(token.last_used_at ?? '')}</td>
                      <td className="px-4 py-3 text-slate-300">{formatDate(token.expires_at ?? '')}</td>
                      <td className="px-4 py-3 text-right">
                        <Button variant="danger" disabled={revokeTokenMutation.isPending || token.revoked} onClick={() => revokeTokenMutation.mutate(token.id)}>
                          {token.revoked ? 'Revoked' : 'Revoke'}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-white">Audit Log</h2>
              <p className="mt-1 text-sm text-slate-400">Latest RBAC and security activity from /aml/auth/audit.</p>
            </div>
            <Badge variant="blue">{auditEvents.length}</Badge>
          </div>

          {auditEvents.length === 0 ? (
            <div className="mt-4 rounded-md border border-dashed border-quantum-border bg-quantum-panel px-4 py-6 text-sm text-slate-400">
              No audit events recorded.
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto rounded-md border border-quantum-border">
              <table className="min-w-full text-sm">
                <thead className="bg-quantum-sidebar text-left text-xs uppercase tracking-[0.18em] text-slate-400">
                  <tr>
                    <th className="px-4 py-3 font-medium">Timestamp</th>
                    <th className="px-4 py-3 font-medium">User</th>
                    <th className="px-4 py-3 font-medium">Action</th>
                    <th className="px-4 py-3 font-medium">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {auditEvents.map((event, index) => (
                    <tr key={event.id} className={index % 2 === 0 ? 'bg-quantum-north' : 'bg-quantum-panel'}>
                      <td className="px-4 py-3 text-slate-300">{formatDate(event.created_at)}</td>
                      <td className="px-4 py-3 text-slate-300">{event.username || 'system'}</td>
                      <td className="px-4 py-3 text-slate-200">{toTitleCase(event.action || event.event_type)}</td>
                      <td className="px-4 py-3 text-slate-300">
                        <div title={JSON.stringify(event.details)}>{truncateDetails(event)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
