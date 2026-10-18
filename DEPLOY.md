# Deploying Vesalius (free)

A public showcase plus one admin login that unlocks uploads, pipeline runs and proofs. Everything
below is free (checked October 2026):

- an Oracle Cloud **Always Free** Ampere A1 VM (arm64)
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
Oracle's A1 is the only always-free machine that can run a segmentation:
- Free container platforms stop at 1 GB of RAM.
- Hugging Face Docker Spaces now need a paid plan.
- Google, AWS and Azure give 1 GB, or credits that run out.

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
2. **Oracle Cloud.** Sign up at <https://signup.cloud.oracle.com>.
   - A phone number and a card are needed for identity; Always Free resources are not charged.
   - Pick a **home region** with Ampere A1 capacity. It can't be changed later, and all free
     compute lives there.
   - Stay on the free account (the decision for this deployment): no Pay As You Go upgrade, so
     the card can never be billed.
3. **DuckDNS.** Log in at <https://www.duckdns.org>, create a subdomain (e.g. `vesalius-yourname`),
   and copy the **token**.
4. **Groq (optional).** A free API key from <https://console.groq.com> for the AI explanations.
   You put it in `.env` on the server yourself (step 4).

## 2 · The VM (you, in the Oracle console)

1. Compute → Instances → Create:
   - **Image:** Ubuntu 24.04.
   - **Shape:** Ampere `VM.Standard.A1.Flex`, **2 OCPU / 12 GB**.
     - **Not more.** Since 15 June 2026 that is the whole free allowance (it was 4 / 24).
     - If the account's A1 usage is above it when the trial ends, Oracle deletes the A1 instances.
   - **Boot volume:** 100 GB (the free allowance is 200 GB of block storage in total).
   - **SSH key:** add your public key.
   - **"Out of host capacity" is common.** Retry in another availability domain, or later.
2. Networking → the VCN → Security List: add ingress rules for **TCP 80 and 443** from
   `0.0.0.0/0`. Port 22 is already there.
3. Point the DuckDNS subdomain at the VM's public IP. `setup-server.sh` keeps it updated.

## 3 · Install (on the VM)

```bash
ssh ubuntu@<vm-ip>
git clone https://github.com/<you>/<repo>.git ~/vesalius      # public: no key needed
cd ~/vesalius
sudo bash scripts/deploy/setup-server.sh vesalius-yourname.duckdns.org <duckdns-token>
exit   # log in again so the docker group applies
```

`setup-server.sh` (idempotent: safe to run again):
- installs Docker
- opens only 22/80/443
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
  - It is unmeasured on 2 A1 cores: allow an hour, and write the real time here after the first run.

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

- **Idle reclamation.** Oracle may reclaim an Always Free instance when its CPU (95th percentile),
  network and memory all stay under 20% for 7 days.
  - A quiet showcase can meet that. Uptime pings don't prevent it, because they measure answers,
    not load.
  - With the free-only decision, the defences are the rebuild path above and the UptimeRobot
    alert.
  - Measure the stack's resident memory against the 20% line (2.4 GB of 12) after the first
    deploy, and note it here.
- **2 cores, 12 GB, one heavy job at a time.**
  - A segmentation, proof or run holds a lock while the others wait (`api/demo_state.HEAVY_LOCK`).
  - Memory: api 8 GB, Postgres 1 GB, MinIO 1 GB, Caddy 256 MB, plus 4 GB of swap.
- **arm64.**
  - The image is built natively on GitHub's arm64 runner and smoke-tested there: every heavy import
    plus the unit suite, before it is pushed.
  - PyTorch comes from its CPU index; `torch 2.14.0+cpu` has an aarch64 wheel.
  - S10 decimates with fast-simplification, because pymeshlab had no arm64 build.
- **One admin, no user accounts.** Out of scope by design.
- **Never upload real patient scans**, not even as the admin (CLAUDE.md hard rule 1).
- **Free services change.** This file reflects October 2026 (Oracle halved A1 in June 2026 with no
  announcement). Recheck the limits before relying on them.
