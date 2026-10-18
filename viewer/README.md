# Vesalius app

The product's single app, served by the pipeline's API at `/app/` (see the repo `README.md`).

| Route | Screen |
|---|---|
| `/app/` | Dashboard |
| `/app/pipeline` | Run a scan: samples, upload, live progress, results |
| `/app/studies` | Every published study |
| `/app/hologram/:id` | A study, full screen, hands or mouse (`surface:<series id>` for bone / skin results) |
| `/app/body` | The full-body atlas (Holoscan, 8 systems) |
| `/app/proofs` | The five proofs |

```bash
npm ci
npm run dev     # http://localhost:5173/app/, proxies the API (VESALIUS_API, default :8001)
npm test        # vitest
npm run build   # the Docker image does this in its Node stage
```

Holoscan's rendering and gesture rules still hold: read `refs/VIEWER-CONSTRAINTS.md` before
changing `components/`. `context.md` documents Holoscan as it was before it joined Vesalius.
