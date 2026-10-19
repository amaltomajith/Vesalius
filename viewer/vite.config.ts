import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vesalius's app. Served by the pipeline's FastAPI app at /app (same origin as its API, no CORS).
// `--mode pages` builds the static site instead (GitHub Pages, no server; src/lib/runtime.ts):
// VITE_STATIC=1 from .env.pages, served under VITE_BASE (the repository's Pages path).
// In development the API calls and the atlas files are proxied to the running demo instead.
const API = process.env.VESALIUS_API ?? 'http://localhost:8001'

export default defineConfig(({ mode }) => ({
  base: mode === 'pages' ? (process.env.VITE_BASE ?? '/Vesalius/app/') : '/app/',
  plugins: [react()],
  server: {
    fs: { allow: ['.'] },
    proxy: Object.fromEntries(
      ['/studies', '/artifacts', '/contracts', '/series', '/demo', '/ai', '/app/atlas'].map((p) => [p, { target: API, changeOrigin: true }]),
    ),
  },
  optimizeDeps: {
    // Let Vite auto-discover three / r3f deps -- manual include + sub-path exclude
    // caused a re-optimisation loop that left .vite/deps in a locked state.
    include: ['three', 'zustand'],
  },
  build: {
    rollupOptions: { input: 'index.html' },
  },
}))
