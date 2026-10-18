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
    <div className="page page--dash">
      <section className="tile login-tile" data-testid="login">
        <h1>Admin sign in</h1>
        <p className="tile-note">
          This is a public showcase. Everyone can explore the studies, the full body and the proof
          results; running the pipeline is for the admin.
          {!isPublic && ' On this machine everything is already open.'}
        </p>
        <form className="login-form" onSubmit={(e) => void submit(e)}>
          <label className="login-field">
            <span><LockSimple size={15} /> Admin password</span>
            <input type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required data-testid="admin-password" />
          </label>
          {error && <p className="login-error" role="alert">{error}</p>}
          <div className="gate-tile__actions">
            <button className="btn btn-dark" type="submit" disabled={busy || !password}>Sign in</button>
            <Link className="btn" to="/">Back to the dashboard</Link>
          </div>
        </form>
      </section>
    </div>
  );
}
