import { useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';
import { login } from '../api/auth';
import Button from '../components/ui/Button';
import Card from '../components/ui/Card';
import ErrorMessage from '../components/ui/ErrorMessage';
import Spinner from '../components/ui/Spinner';
import { useAuth } from '../lib/auth-context';

export default function Login() {
  const location = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('password');
  const [error, setError] = useState<unknown>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const redirectPath = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return params.get('redirect') || '/';
  }, [location.search]);

  if (auth.isChecking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-quantum-panel">
        <Spinner />
      </div>
    );
  }

  if (auth.isAuthenticated) {
    return <Navigate to={redirectPath} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setIsSubmitting(true);
    setError(null);

    try {
      await login(username, password);
      auth.markAuthenticated(username);
      navigate(redirectPath, { replace: true });
    } catch (submitError) {
      setError(submitError);
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-quantum-panel px-4 py-10 text-slate-100">
      <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[1.2fr,0.8fr]">
        <Card className="bg-quantum-north p-8">
          <div className="text-xs uppercase tracking-[0.32em] text-slate-500">Quantum operator console</div>
          <h1 className="mt-3 text-4xl font-semibold text-slate-100">OpenBlade Sign In</h1>
          <p className="mt-3 max-w-2xl text-sm text-slate-400">
            Sign in to OpenBlade to access the dashboard, physical map views, and credential-aware
            operator workflows.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {[
              ['Modern SaaS dashboard', 'Operational posture, jobs, and library telemetry.'],
              ['Physical library map', 'Partition, drive, and IE station visibility.'],
              ['Credential store', 'Cookie-backed AML auth for secure workflows.'],
            ].map(([title, copy]) => (
              <div key={title} className="rounded-md border border-quantum-border bg-quantum-info px-4 py-4">
                <div className="text-sm font-semibold text-slate-100">{title}</div>
                <div className="mt-2 text-sm text-slate-400">{copy}</div>
              </div>
            ))}
          </div>
        </Card>

        <Card className="bg-quantum-info p-8">
          <div className="text-xs uppercase tracking-[0.26em] text-slate-500">Authentication</div>
          <h2 className="mt-1 text-2xl font-semibold text-slate-100">Sign in to OpenBlade</h2>
          <p className="mt-2 text-sm text-slate-400">Default OpenBlade development credentials are prefilled for local use.</p>

          <form className="mt-6 space-y-4" onSubmit={handleSubmit}>
            <div>
              <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-slate-500">Username</label>
              <input
                className="w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                required
              />
            </div>
            <div>
              <label className="mb-2 block text-xs uppercase tracking-[0.18em] text-slate-500">Password</label>
              <input
                className="w-full rounded-md border border-quantum-border bg-quantum-panel px-3 py-2 text-sm text-slate-100 outline-none ring-0 transition focus:border-quantum-red"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
            </div>
            <Button type="submit" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? 'Signing in…' : 'Sign in'}
            </Button>
          </form>

          {error ? <div className="mt-4"><ErrorMessage error={error} title="Authentication failed" /></div> : null}
        </Card>
      </div>
    </div>
  );
}
