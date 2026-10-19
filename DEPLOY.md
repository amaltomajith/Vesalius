# Deploying Vesalius

Two parts, both without a credit card (checked October 2026):

- **A. The static site on GitHub Pages: free, always on, no server.**
  - It holds the landing page, the app and the full-body atlas.
  - It also holds the three public CT studies, exported from the pipeline's gold output, and the
    recorded proof results.
  - It keeps working forever, whatever happens to part B.
- **B. The live pipeline on Azure for Students: optional, woken on demand.**
  - Runs, proofs and the AI happen here. The VM is off until a visitor presses **Wake the live
    pipeline**; it starts in about 2 to 3 minutes and deallocates itself when nobody uses it.
  - Credit is spent only while it runs. When the yearly $100 is used up, Azure disables the
    subscription instead of billing you, and part A carries on.

```
GitHub Pages (free, always on)                       Azure for Students ($100/yr, spent only when used)
<you>.github.io/Vesalius/                            ┌ wake function (Flex Consumption, free grant)
  landing · app/ (static mode) · app/atlas/          │   GET /api/status · POST /api/wake (daily cap)
  app/data/ (studies, proofs, from gold)             │
  "Wake the live pipeline" ──────────────────────────┘── starts ──> VM, Arm64 B4ps_v2 (normally off)
     └─ when ready: opens https://<name>.duckdns.org/app/...        Caddy + api + Postgres + MinIO
                                                                    autostop.sh: idle → deallocate
```

**Why this shape:** research into free hosting, October 2026 (the report "Free hosting for Phantom", written before the product was renamed Vesalius).
- Oracle's Always Free A1 is the only always-free machine big enough for a segmentation, but its
  signup needs a card; it is the alternative at the end.
- Free container platforms stop at 1 GB of RAM; Hugging Face Docker Spaces need a paid plan.
- An always-on Azure VM would spend the $100 in about four months. Off by default, it lasts the year.

## What visitors can do

| Where | What |
|---|---|
| Static site | Explore the three studies and the full body (mouse or hands), see the audit trail of every study, read the recorded proof results |
| Live pipeline, once woken | Run the built-in sample scans (synthetic or public test files, never a patient: 6 an hour each, `VESALIUS_RUN_DAILY_CAP` 60 a day for the site), run the proofs (3 an hour), ask the AI (10 an hour, `VESALIUS_AI_DAILY_CAP` 300 a day) |
| Admin only (`/app/login`) | Upload a scan, `/ingest`: so nobody can send it a real patient scan (CLAUDE.md hard rule 1) |

The static site sends nothing to anyone. Its one request anywhere other than its own files is the
wake call, which carries no data (`viewer/src/lib/wake.ts`, guarded in `test_viewer_serving.py`).

## A · The static site (GitHub Pages)

`.github/workflows/pages.yml` builds and deploys it.

1. **Turn Pages on:** repository → Settings → Pages → Source: **GitHub Actions**.
2. **Build the data once:** Actions → **pages** → Run workflow, tick **rebuild the exported
   studies** (about an hour). The `data` job:
   - runs the pipeline on the three public CTs
   - runs every proof (proof 5, "a head study in the viewer has no face", must pass)
   - exports the gold with `scripts/export_static.py`, which reads only the gold serving routes
     and refuses an undefaced head, a mesh matching no gold record, or a study without a public
     source to credit
   - publishes it as the asset of the `static-data` release. Scan data is never committed.
3. **After that, every push to `main` redeploys the site** around the latest data, in a few
   minutes. Rebuild the data only when the pipeline changes.

The site is then at `https://<you>.github.io/Vesalius/`.

**Rehearse locally** (the same layout):
```bash
cd viewer && VITE_BASE=/Vesalius/app/ npm run build -- --mode pages && cd ..
python scripts/export_static.py --api http://127.0.0.1:<port> --out site-data   # needs proof 5 passed
bash scripts/build_pages_site.sh site site-data
```

## B · The live pipeline (Azure for Students)

Without part B the site works fully; the "Wake" buttons just say the live pipeline isn't offered.

### 1. Accounts (you)
- **Azure for Students:** activate at <https://azure.microsoft.com/free/students> with your
  school email. No card. Never upgrade the subscription to Pay As You Go, so nothing can be
  billed.
- **DuckDNS:** log in at <https://www.duckdns.org>, create a subdomain (e.g. `vesalius-yourname`),
  and copy the **token**.

### 2. Provision (you, in Azure Cloud Shell)
Open the portal → the `>_` icon (Cloud Shell, Bash). It is already signed in, so no credential
leaves Azure.

```bash
git clone https://github.com/amaltomajith/Vesalius.git && cd Vesalius
bash deploy/azure/provision.sh vesalius-yourname centralindia
```

`provision.sh` (idempotent) creates the VM, a custom role and the wake function:
- **The VM:** Ubuntu 24.04 **Arm64** `Standard_B4ps_v2` (4 vCPU / 16 GB, a segmentation fits), a
  32 GB Standard SSD, a static IP, inbound 22/80/443 only, and a managed identity.
- **The role "Vesalius VM power":** read, start and deallocate, assigned on that VM only, to the
  VM itself (to put itself to sleep) and to the wake function (to start it).
- **The wake function** (`deploy/azure/wake`): Flex Consumption, callable only from the site
  (CORS), at most 6 wakes a day.

It prints the IP and the two repository variables. If Azure refuses something:
- **A size or region refused** (student subscriptions allow only some): rerun with
  `VM_SIZE=...` or another region.
- **Flex Consumption missing in your region:** see `az functionapp list-flexconsumption-locations`
  and rerun with `WAKE_LOCATION=<region>`.

Then **Cost Management → Budgets → Add:** $100, with email alerts at 50 % and 80 %.

### 3. The server (you, over SSH from Cloud Shell)
Point the DuckDNS subdomain at the printed IP, then:

```bash
ssh azureuser@<ip>
git clone https://github.com/amaltomajith/Vesalius.git ~/vesalius && cd ~/vesalius
sudo bash scripts/deploy/setup-server.sh vesalius-yourname.duckdns.org <duckdns-token> --autostop
exit   # log in again so the docker group applies
```

`setup-server.sh` (idempotent):
- installs Docker
- adds 4 GB of swap
- installs the DuckDNS updater
- turns on automatic security updates
- writes `.env`: random secrets, the domain, the CI image name
- `--autostop`: installs the timer that runs `scripts/deploy/autostop.sh` every minute

Optional, the AI: `nano ~/vesalius/.env` and set `GROQ_API_KEY=` to your key. You type it;
nothing else ever writes it. Then:

```bash
cd ~/vesalius && bash scripts/deploy/deploy.sh --studies
```

The first run:
- **Asks for the admin password** (12+ characters); only its hash goes into `.env`.
- **Pulls the arm64 api image** CI built (`ghcr.io/<you>/vesalius-api`).
- **Builds the MinIO image once**, from MinIO's final upstream release (`docker/minio`): MinIO no
  longer publishes images.
- **Loads the three public CT studies**, segmenting one at a time. Unmeasured on this VM: allow an
  hour, and write the real time here.

Keep the session alive while it loads: autostop only stops an idle VM, and the load counts as busy.

### 4. Connect the site (you, on GitHub)
Repository → Settings → Secrets and variables → Actions → **Variables**: add `WAKE_URL` and
`BACKEND_URL` as printed by `provision.sh`. Re-run the **pages** workflow (no data rebuild
needed). The site's Wake buttons now work.

### How it sleeps and wakes
- **Waking:** the site posts to the wake function, which starts the VM if it is deallocated and
  today's wakes are under the cap. Docker brings the stack up at boot, and the site polls until
  `/health/ready` answers (database and object store), then opens the same screen on the backend.
- **Sleeping:** `autostop.sh` asks the api's `/health/idle` every minute (health checks don't
  count as use). It deallocates the VM through its managed identity when it is idle for 15
  minutes, or has been up 60, never mid-scan or mid-proof before the 3-hour hard limit. Tune it
  with `VESALIUS_IDLE_MINUTES`, `VESALIUS_SESSION_MINUTES` and `VESALIUS_HARD_MINUTES` in `.env`.
- **The address stays the same:** the static IP keeps DuckDNS and Caddy's certificates valid
  across restarts.

### Costs (Central India list prices, October 2026; the portal shows today's)
| | |
|---|---|
| Asleep | static IP ≈ $3.65 + 32 GB disk ≈ $2.40 + function storage ≈ $0.10 → **≈ $6 a month** |
| Awake | B4ps_v2 ≈ **$0.09 an hour**: a 20-minute visit ≈ $0.03 |
| Worst case | 6 wakes a day × at most 60 minutes ≈ $16 a month on top |

With light traffic, about $7 to $8 a month: the $100 lasts about a year, and it renews each school
year. When it runs out the subscription is disabled, not billed; the static site is unaffected.

### Check it
```bash
D=vesalius-yourname.duckdns.org
curl -s  https://$D/auth/me                                  # {"public":true,"admin":false}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$D/demo/runs/upload   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/bronze/x             # 404
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/silver/x             # 404
```
Then from the static site, wake it, run a sample scan, run a proof, ask the AI. Leave it 15
minutes and check that the VM is **Stopped (deallocated)** in the portal. Note the cold-start time
here.

## Public mode on the backend

`VESALIUS_PUBLIC=1` (set by `docker-compose.prod.yml`):
- **Visitors:** everything to look at, plus the rate-limited actions in the table above.
- **The admin:** one password, stored only as a scrypt hash in `.env`. The session cookie is
  signed, HttpOnly, Secure, SameSite=Strict, and lasts 12 hours. After 5 wrong passwords from one
  IP, that IP is refused for 10 minutes.
- **A unit test walks the real route table** and fails if a route that changes anything has
  neither the admin guard nor its own rate limit, or if uploads stop being admin-only.
- **Downloads:** presigned links are signed for `https://files.<domain>`, and Caddy lets through
  only reads of the gold bucket. Bronze, silver, quarantine and the MinIO console are unreachable.

## If the VM is lost

There is nothing to back up. Every scan is public and re-created exactly: `deploy.sh --studies`
rebuilds the same gold, and the reproducibility proof guarantees byte-identical output. The only
state is `.env`; keep a private copy of it. To rebuild, rerun §2 to §4.

## Alternative: Oracle Cloud Always Free (needs a card)

The backend at no cost at all and always on, if you can pass Oracle's card check (a temporary
verification hold; Always Free resources are never charged unless the account is upgraded):
1. **Sign up** at <https://signup.cloud.oracle.com>. Pick a home region with Ampere A1 capacity;
   it can't be changed. Never upgrade to Pay As You Go.
2. **Create the instance:** Compute → Instances → Create, Ubuntu 24.04, `VM.Standard.A1.Flex` at
   **2 OCPU / 12 GB**, no more (since 15 June 2026 that is the whole free allowance). Boot volume
   100 GB.
3. **Open the ports:** in the VCN security list, add ingress TCP 80 and 443.
4. **Install:** §3 above as `ubuntu`, without `--autostop`. Then set only `BACKEND_URL`, not
   `WAKE_URL`, and the site links straight to it.

Oracle may reclaim an idle Always Free instance (CPU, network and memory under 20 % for 7 days);
rebuilding is the defence.

## Limits worth knowing
- **One heavy job at a time.** A segmentation, proof or run holds a lock while others wait
  (`api/demo_state.HEAVY_LOCK`).
- **The live pipeline's in-memory state resets on every wake.** That covers runs in progress, the
  last proof results and rate-limit counters. The static site keeps its own recorded proofs.
- **arm64.** CI builds and smoke-tests the api image and the MinIO image on GitHub's native arm64
  runner. S10 decimates with fast-simplification because pymeshlab has no arm64 build.
- **Never upload real patient scans,** not even as the admin (CLAUDE.md hard rule 1).
- **Free services change.** This file reflects October 2026; recheck limits before relying on them.
