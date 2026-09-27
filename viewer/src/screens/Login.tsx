import { useState, type FormEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { LockSimple } from '@phosphor-icons/react';
import { useAuthStore } from '../store/authStore';

/** Admin sign-in for a public deployment. Visitors never need it to look around. */
export default function Login() {
  const login = useAuthStore((s) => s.login);
  const isPublic = useAuthStore((s) => s.isPublic);
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = params.get('next') ?? '/pipeline';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(password);
      navigate(next.startsWith('/') ? next : '/pipeline');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setPassword('');
    }
  };

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <p className="kicker mono">Admin</p>
          <h1>Sign in to run scans and proofs</h1>
          <p className="lede">
            This is a public showcase. Everyone can explore the studies, the full body and the proof
            results; running the pipeline is for the admin.
            {!isPublic && ' (On this machine everything is already open.)'}
          </p>
        </div>
      </header>
      <form className="card login-card" onSubmit={(e) => void submit(e)}>
        <label className="field">
          <span className="field-label"><LockSimple size={15} /> Admin password</span>
          <input type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required data-testid="admin-password" />
        </label>
        {error && <p className="login-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={busy || !password}>Sign in</button>
          <Link className="btn" to="/">Back to the dashboard</Link>
        </div>
      </form>
    </div>
  );
}
