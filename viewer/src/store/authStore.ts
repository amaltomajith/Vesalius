import { create } from 'zustand';

// Public mode (deployment): everyone may look, only the admin may run scans, proofs or the AI.
// Locally the server reports {public: false, admin: true} and nothing in the app changes.
interface AuthState {
  loaded: boolean;
  isPublic: boolean;
  admin: boolean;
  refresh: () => Promise<void>;
  login: (password: string) => Promise<void>;
  logout: () => Promise<void>;
}

async function post(path: string, body?: unknown): Promise<{ public: boolean; admin: boolean }> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
  const data = (await r.json().catch(() => ({}))) as { public?: boolean; admin?: boolean; detail?: string };
  if (!r.ok) throw new Error(data.detail ?? `Sign-in failed (${r.status}).`);
  return { public: !!data.public, admin: !!data.admin };
}

export const useAuthStore = create<AuthState>((set) => ({
  loaded: false,
  isPublic: false,
  admin: true,
  refresh: async () => {
    try {
      const r = await fetch('/auth/me', { credentials: 'same-origin' });
      const me = (await r.json()) as { public: boolean; admin: boolean };
      set({ loaded: true, isPublic: me.public, admin: me.admin });
    } catch {
      set({ loaded: true }); // the server is unreachable: the screens show their own errors
    }
  },
  login: async (password) => {
    const me = await post('/auth/login', { password });
    set({ loaded: true, isPublic: me.public, admin: me.admin });
  },
  logout: async () => {
    const me = await post('/auth/logout');
    set({ loaded: true, isPublic: me.public, admin: me.admin });
  },
}));

/** true when this visitor may run things (always true locally) */
export const useCanAct = () => useAuthStore((s) => !s.isPublic || s.admin);
