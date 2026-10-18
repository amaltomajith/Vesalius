import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vesalius's app. Served by the pipeline's FastAPI app at /app (same origin as its API, no CORS).
// In development the API calls and the atlas files are proxied to the running demo instead.
const API = process.env.VESALIUS_API ?? 'http://localhost:8001'

export default defineConfig({
  base: '/app/',
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
})
