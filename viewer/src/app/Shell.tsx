import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, NavLink, Outlet } from 'react-router';
import { LockSimple, PersonArmsSpread, Play, Plus, ShieldCheck, SignOut, SquaresFour, Stack } from '@phosphor-icons/react';
import { api, type Catalogue } from '../lib/api';
import { useAuthStore } from '../store/authStore';

const NAV = [
  { to: '/', label: 'Dashboard', icon: SquaresFour, end: true },
  { to: '/pipeline', label: 'Run a scan', icon: Play, end: false },
  { to: '/studies', label: 'Studies', icon: Stack, end: false },
  { to: '/body', label: 'Full body', icon: PersonArmsSpread, end: false },
  { to: '/proofs', label: 'Proofs', icon: ShieldCheck, end: false },
];

interface ToastApi { toast: (text: string) => void }
const ToastContext = createContext<ToastApi>({ toast: () => {} });
export const useToast = () => useContext(ToastContext);

type Status = 'idle' | 'busy' | 'offline' | null;

/** The pipeline's state for the top bar: asked on load and every 10 s while the tab is visible. */
function usePipelineStatus(): Status {
  const [status, setStatus] = useState<Status>(null);
  useEffect(() => {
    let live = true;
    const ask = () => {
      if (document.hidden) return;
      api<Catalogue>('/demo/datasets')
        .then((c) => { if (live) setStatus(c.busy ? 'busy' : 'idle'); })
        .catch(() => { if (live) setStatus('offline'); });
    };
    ask();
    const id = window.setInterval(ask, 10000);
    return () => { live = false; window.clearInterval(id); };
  }, []);
  return status;
}

/** Phantom's frame: a dark top bar over every screen except the immersive hologram. */
export default function Shell({ children }: { children?: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const status = usePipelineStatus();
  // public deployments: everyone looks, the admin signs in to run things (store/authStore.ts)
  const { isPublic, admin, refresh, logout } = useAuthStore();
  useEffect(() => { void refresh(); }, [refresh]);
  const toast = useCallback((text: string) => {
    setMessage(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), 6000);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      <div className="shell">
        <header className="topbar">
          <a className="brand" href="/" title="About Phantom">
            <span className="brand-mark" aria-hidden="true" />
            <span>Phantom</span>
          </a>
          <nav className="topnav" aria-label="Phantom">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => `topnav__item${isActive ? ' topnav__item--on' : ''}`}>
                <Icon size={16} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="topbar__end">
            {status && (
              <span className={`status status--${status}`} role="status">
                <span className="status__light" aria-hidden="true" />
                {status === 'busy' ? 'Pipeline working' : status === 'idle' ? 'Pipeline idle' : 'Pipeline offline'}
              </span>
            )}
            <Link className="icon-btn" to="/pipeline" title="Run a scan" aria-label="Run a scan"><Plus size={18} /></Link>
            {isPublic && (admin ? (
              <button type="button" className="icon-btn" onClick={() => void logout()} title="Sign out" aria-label="Sign out" data-testid="sign-out">
                <SignOut size={17} />
              </button>
            ) : (
              <Link className="icon-btn" to="/login" title="Admin sign in" aria-label="Admin sign in" data-testid="sign-in"><LockSimple size={17} /></Link>
            ))}
          </div>
        </header>
        <main className="shell-main">
          {children ?? <Outlet />}
        </main>
        <nav className="shell-tabs" aria-label="Sections">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => `tab-item${isActive ? ' tab-item--on' : ''}`}>
              <Icon size={20} />
              <span>{label.replace('Run a scan', 'Run')}</span>
            </NavLink>
          ))}
        </nav>
        {message && <div className="toast" role="alert">{message}</div>}
      </div>
    </ToastContext.Provider>
  );
}
