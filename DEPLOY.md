# Deploying Vesalius (free)

A public showcase plus one admin login that unlocks uploads, pipeline runs and proofs. Nothing
below needs a credit card (checked October 2026):

- an arm64 VM on **Azure for Students**: $100 of credit a year, no card, renewable while you are a
  student. When the credit runs out Azure disables the subscription; it never bills you.
  (With a card, Oracle's Always Free A1 runs the same stack at no cost at all: see the end.)
- a **DuckDNS** name, with **Caddy** for HTTPS
- **GitHub Actions** for CI and for building the arm64 image, with **ghcr.io** to hold it
- **UptimeRobot** for alerts

```
internet ──443──> Caddy ──> api:8000        https://<name>.duckdns.org        landing, /app, /studies, /docs
                        └─> minio:9000      https://files.<name>.duckdns.org  GET/HEAD /gold/* only
                 (postgres, minio, api: no host ports)

GitHub (public repo) ── CI on ubuntu-24.04-arm ──> ghcr.io/<you>/vesalius-api ──pull──> the VM
```

Locally nothing changes: `Start Demo.bat` still runs everything on `127.0.0.1` with no login.

**Why this stack:** research into free hosting, October 2026 (the report "Free hosting for Phantom", written before the product was renamed Vesalius).
- Oracle's A1 is the only always-free machine that can run a segmentation, but its signup needs a
  card.
- Free container platforms stop at 1 GB of RAM; Hugging Face Docker Spaces now need a paid plan.
- Azure for Students needs only a school email. Its free B1s VM (1 GB, x64) is too small, so the
  VM spends credit, and Azure bills by the hour: segment on a big size for an hour, then serve on
  a small one.

## What public mode does

`VESALIUS_PUBLIC=1` (set by `docker-compose.prod.yml`):

- **Visitors can look:** landing, app, studies, hologram, full-body atlas, lineage, reports, proof
  verdicts, API docs, gold downloads.
- **Visitors can ask the AI**, within limits:
  - 10 explanations an hour per visitor
  - 300 a day for the whole site (`VESALIUS_AI_DAILY_CAP`), so the free Groq quota can't be drained
  - Snapshots are sent only for the atlas, never for a patient study (CLAUDE.md hard rule 8).
- **Only the admin can act:** `POST /demo/runs/*`, `POST /demo/proofs/*` and `/ingest` return 401
  to everyone else.
  - A unit test walks the real route table and fails if a new mutating route has neither the admin
    guard nor, for the two AI routes, the rate limit.
  - Public uploads are off because this project must never receive real patient data (hard rule 1).
- **Admin login:** at `/app/login` (the lock icon in the top bar).
  - One password, stored only as a scrypt hash in `.env`.
  - The session cookie is signed, HttpOnly, Secure, SameSite=Strict, and lasts 12 hours.
  - After 5 wrong passwords from one IP, that IP is refused for 10 minutes.
- **Downloads:** presigned links are signed for `https://files.<domain>`, and Caddy lets through only
  reads of the gold bucket. Bronze, silver, quarantine and the MinIO console are unreachable.

## 1 · Accounts (you)

1. **GitHub.** Create a **public** repository and push this branch to it as `main`. CI
   (`.github/workflows/ci.yml`) then builds the arm64 api image and publishes it as
   `ghcr.io/<you>/vesalius-api`.
   - After the first green run, open the package on GitHub (your profile → Packages →
     `vesalius-api` → Package settings) and set its visibility to **Public**. ghcr.io packages
     start private even for public repositories, and the server pulls without a login.
2. **Azure for Students.** Activate it at <https://azure.microsoft.com/free/students> with your
   school email. No card.
   - Never upgrade the subscription to Pay As You Go: then nothing can ever be billed, and when the
     $100 is spent the VM simply stops.
3. **DuckDNS.** Log in at <https://www.duckdns.org>, create a subdomain (e.g. `vesalius-yourname`),
   and copy the **token**.
4. **Groq (optional).** A free API key from <https://console.groq.com> for the AI explanations.
   You put it in `.env` on the server yourself (step 4).

## 2 · The VM (you, in the Azure portal)

The sizes are **Arm64** (the `p` in the name), so they run the image CI builds. Prices are
Central India list prices as tracked in October 2026; the portal shows the current ones before you
create.

| Size | vCPU / RAM | ≈ per hour | Use |
|---|---|---|---|
| `Standard_B4ps_v2` | 4 / 16 GB | $0.09 | the first deploy: loading the studies segments them (~8 GB peak) |
| `Standard_B2pls_v2` | 2 / 4 GB | $0.022 (≈ $16 a month) | serving: the whole stack idles at ~0.6 GB |

Plus about $9 a month for the disk and the static IP. Serving all month costs about $25, so the
$100 lasts about 4 months; **Stop** the VM when nobody is looking and it lasts much longer.

1. Virtual machines → Create → Azure virtual machine:
   - **Region:** Central India, or the nearest the portal offers. Student subscriptions allow only
     some regions and sizes; if a size is unavailable, try another region.
   - **Image:** Ubuntu Server 24.04 LTS, **Arm64**.
   - **Size:** `Standard_B4ps_v2` for now (resized down in §4).
   - **Authentication:** SSH public key, username `azureuser`.
   - **Inbound ports:** SSH (22), HTTP (80), HTTPS (443).
   - **Disks:** OS disk 64 GB, Standard SSD.
   - **Networking:** a new public IP (Standard SKU, which is static).
   - **Management:** auto-shutdown **off** (it would take the site down every night).
2. Cost Management → Budgets: a budget of $100 with alerts at 50 % and 80 %, to your email.
3. Point the DuckDNS subdomain at the VM's public IP. `setup-server.sh` keeps it updated.

## 3 · Install (on the VM)

```bash
ssh azureuser@<vm-ip>
git clone https://github.com/amaltomajith/Vesalius.git ~/vesalius   # public: no key needed
cd ~/vesalius
sudo bash scripts/deploy/setup-server.sh vesalius-yourname.duckdns.org <duckdns-token>
exit   # log in again so the docker group applies
```

`setup-server.sh` (idempotent: safe to run again):
- installs Docker
- opens 80/443 in the host firewall where the image has one (Oracle's); on Azure the network
  security group from §2 is the firewall
- adds 4 GB of swap
- installs the DuckDNS updater
- turns on automatic security updates
- writes `.env`:
  - random database, MinIO and de-identification secrets
  - the domain and a session secret
  - `VESALIUS_IMAGE`, read from the git remote

## 4 · Deploy

Optional, the AI: `nano ~/vesalius/.env` and set `GROQ_API_KEY=` to your key. You type it; nothing
else ever writes it.

```bash
cd ~/vesalius
bash scripts/deploy/deploy.sh --studies
```

The first run:
- **Asks for the admin password** (12+ characters). Only its hash goes into `.env`.
- **Pulls the arm64 image** CI built (`docker pull`, a few minutes). `--build` builds it here
  instead, if CI is ever unavailable (slow on 2 cores).
- **Builds the MinIO image once**, from MinIO's final upstream release (`docker/minio/Dockerfile`):
  MinIO no longer publishes images. A few minutes; CI proves the same build on arm64.
- **Starts the stack and loads the three public CT studies** (head, chest, abdomen). That downloads
  the scans and the segmentation model and segments one study at a time.
  - It is unmeasured on 4 Azure Arm64 cores: allow an hour, and write the real time here after the
    first run.

**Then resize down** (Azure): once the studies are loaded, VM → Size → `Standard_B2pls_v2`. The
VM restarts and the stack comes back by itself (Docker starts at boot, the services restart).
Before an admin "Run a scan" or proof, resize back up to `Standard_B4ps_v2`: a segmentation does
not fit in 4 GB.

Later updates: push to `main`, wait for CI to go green, then `bash scripts/deploy/deploy.sh`. It
pulls the code and the new image and restarts.

**Uptime:** add two free UptimeRobot HTTP monitors, `https://<domain>/health` and
`https://<domain>/`, with alerts to your email.

## 5 · Check it

From your own machine:

```bash
D=vesalius-yourname.duckdns.org
curl -sI https://$D/ | head -1                               # 200
curl -s  https://$D/auth/me                                  # {"public":true,"admin":false}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$D/demo/proofs/face   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/bronze/x             # 404
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/silver/x             # 404
nmap -Pn -p 1-10000 $D                                       # only 22, 80, 443 open
```

Then in a browser:
- open `/app`, a study, the hologram (the camera prompt appears because it's HTTPS) and the full body
- ask the AI about a structure
- sign in at `/app/login` and run proof 5 ("the 3D viewer never shows a face")

## If the VM is lost (reclaimed, deleted, or no longer reachable)

There is nothing to back up.
- **Every scan is public and re-created exactly.** `deploy.sh --studies` rebuilds the same gold,
  and the reproducibility proof guarantees byte-identical output.
- **The only state is `.env`:** keep a private copy of it (secrets, admin hash, Groq key). A lost
  `.env` only means new secrets and a new admin password.

To rebuild:
1. create a new VM (§2)
2. run setup-server (§3)
3. copy your saved `.env` back (or let setup-server make a new one)
4. run `deploy.sh --studies`

About half an hour of your time, plus the study load.

`scripts/deploy/backup.sh` still exists if you want nightly dumps on the VM, but they live on the
same machine, so they don't survive losing it.

## Limits worth knowing

- **The credit is the clock (Azure).** About 4 months of always-on serving per $100.
  - Check what is left in the portal's Education hub, and keep the budget alerts.
  - Stop (deallocate) the VM between showings: a stopped VM costs only its disk and IP.
  - When the credit is spent the subscription is disabled, not billed; it renews next school year.
- **One heavy job at a time.**
  - A segmentation, proof or run holds a lock while the others wait (`api/demo_state.HEAVY_LOCK`).
  - Memory limits: api 8 GB, Postgres 1 GB, MinIO 1 GB, Caddy 256 MB, plus 4 GB of swap. On the
    4 GB serving size these are ceilings, not reservations: idle, the stack uses ~0.6 GB.
- **arm64.**
  - The image is built natively on GitHub's arm64 runner and smoke-tested there: every heavy import
    plus the unit suite, before it is pushed.
  - PyTorch comes from its CPU index; `torch 2.14.0+cpu` has an aarch64 wheel.
  - S10 decimates with fast-simplification, because pymeshlab had no arm64 build.
- **One admin, no user accounts.** Out of scope by design.
- **Never upload real patient scans**, not even as the admin (CLAUDE.md hard rule 1).
- **Free services change.** This file reflects October 2026 (Oracle halved A1 in June 2026 with no
  announcement). Recheck the limits before relying on them.

## Alternative: Oracle Cloud Always Free (needs a card)

The same stack at no cost at all, if you can pass Oracle's card check (a temporary verification
hold; Always Free resources are never charged unless the account is upgraded).

1. Sign up at <https://signup.cloud.oracle.com>. Pick a **home region** with Ampere A1 capacity:
   it can't be changed later, and all free compute lives there. Never upgrade to Pay As You Go.
2. Compute → Instances → Create:
   - **Image:** Ubuntu 24.04. **Shape:** Ampere `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**, not
     more: since 15 June 2026 that is the whole free allowance (it was 4 / 24), and if the
     account's A1 usage is above it when the trial ends, Oracle deletes the A1 instances.
   - **Boot volume:** 100 GB (the free allowance is 200 GB in total). **SSH key:** yours.
   - "Out of host capacity" is common: retry in another availability domain, or later.
3. Networking → the VCN → Security List: ingress **TCP 80 and 443** from `0.0.0.0/0`.
4. §3 and §4 as above, logging in as `ubuntu`. No resizing: 12 GB holds a segmentation.

**Idle reclamation:** Oracle may reclaim an Always Free instance when its CPU (95th percentile),
network and memory all stay under 20% for 7 days. A quiet showcase can meet that, and uptime pings
don't prevent it; the defences are the rebuild path above and the UptimeRobot alert.
