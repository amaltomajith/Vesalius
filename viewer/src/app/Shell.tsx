import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { NavLink, Outlet } from 'react-router';
import { PersonArmsSpread, Play, ShieldCheck, SquaresFour, Stack, ArrowUpRight, LockSimple, SignOut } from '@phosphor-icons/react';
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

/** Phantom's one dashboard: every screen except the immersive hologram lives in this frame. */
export default function Shell({ children }: { children?: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const { isPublic, admin, refresh, logout } = useAuthStore();
  useEffect(() => { void refresh(); }, [refresh]);
  const timer = useRef<number | undefined>(undefined);
  const toast = useCallback((text: string) => {
    setMessage(text);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), 6000);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      <div className="shell">
        <aside className="shell-nav" aria-label="Phantom">
          <a className="brand" href="/" title="About Phantom">
            <span className="brand-mark" aria-hidden="true" />
            <span>Phantom</span>
          </a>
          <nav className="nav-list">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink key={to} to={to} end={end} className={({ isActive }) => `nav-item${isActive ? ' nav-item--on' : ''}`}>
                <Icon size={18} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          {isPublic && (admin ? (
            <button className="nav-auth" onClick={() => void logout()} data-testid="sign-out">
              <SignOut size={15} /> Sign out
            </button>
          ) : (
            <NavLink className="nav-auth" to="/login"><LockSimple size={15} /> Admin sign in</NavLink>
          ))}
          <a className="nav-about" href="/">
            About Phantom <ArrowUpRight size={13} />
          </a>
        </aside>
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
