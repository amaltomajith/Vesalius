# Deploying Phantom (free)

A public, read-only showcase plus one admin login that unlocks uploads, pipeline runs, proofs and
the optional AI. Everything below is free: an Oracle Cloud **Always Free** ARM VM, a **DuckDNS**
name, **Caddy** for HTTPS, **GitHub Actions** for CI, **UptimeRobot** for alerts.

```
internet ──443──> Caddy ──> api:8000        https://<name>.duckdns.org        landing, /app, /studies, /docs
                        └─> minio:9000      https://files.<name>.duckdns.org  GET/HEAD /gold/* only
                 (postgres, minio, api: no host ports)
```

Locally nothing changes: `Start Demo.bat` still runs everything on `127.0.0.1` with no login.

## What public mode does

`PHANTOM_PUBLIC=1` (set by `docker-compose.prod.yml`):

- **Visitors can look:** landing, app, studies, hologram, full-body atlas, lineage, reports, proof
  verdicts, API docs, gold downloads.
- **Only the admin can act:** `POST /demo/runs/*`, `POST /demo/proofs/*`, `/ingest`, `/ai/explain/*`
  return 401 to everyone else. A unit test fails if a new mutating route forgets the guard.
- **Admin login:** at `/app/login`. One password, stored only as a scrypt hash in `.env`. The
  session cookie is signed, HttpOnly, Secure, SameSite=Strict and lasts 12 hours. After 5 wrong
  passwords from one IP, logins from it are refused for 10 minutes.
- **Downloads:** presigned download links are signed for `https://files.<domain>`. Caddy only lets
  through reads of the gold bucket. Bronze, silver, quarantine and the MinIO console are
  unreachable.

## 1 · Accounts (you)

1. **GitHub:** the private repository, with CI in `.github/workflows/ci.yml`.
2. **Oracle Cloud:** sign up at <https://signup.cloud.oracle.com>. A card is needed for identity
   only; Always Free resources are not charged. Pick a home region with Ampere A1 capacity; it
   can't be changed later.
3. **DuckDNS:** log in at <https://www.duckdns.org>, create a subdomain (e.g. `phantom-yourname`),
   and copy the **token**.

## 2 · The VM (you, in the Oracle console)

1. Go to Compute → Instances → Create.
   - **Image:** Ubuntu 24.04.
   - **Shape:** Ampere `VM.Standard.A1.Flex` with **4 OCPU / 24 GB**.
   - **Boot volume:** 100–200 GB.
   - **SSH key:** add your public key.
   - If Oracle says it is "out of capacity", retry later or in another availability domain.
     This is common.
2. Networking → the VCN → Security List → add ingress rules for **TCP 80 and 443** from
   `0.0.0.0/0`. Port 22 is already there.
3. Optional but recommended: turn the account into **Pay As You Go**. You stay free within the
   limits, and idle Always Free VMs can no longer be reclaimed.
4. Point the DuckDNS subdomain at the VM's public IP. `setup-server.sh` keeps it updated.

## 3 · Install (on the VM)

```bash
ssh ubuntu@<vm-ip>
# a read-only deploy key for the private repo:
ssh-keygen -t ed25519 -f ~/.ssh/phantom_deploy -N ""   # add the .pub in GitHub -> repo -> Settings -> Deploy keys
GIT_SSH_COMMAND="ssh -i ~/.ssh/phantom_deploy" git clone git@github.com:<you>/phantom.git ~/phantom
cd ~/phantom && git config core.sshCommand "ssh -i ~/.ssh/phantom_deploy"

sudo bash scripts/deploy/setup-server.sh phantom-yourname.duckdns.org <duckdns-token>
exit   # log in again so the docker group applies
```

`setup-server.sh` does five things:
- installs Docker;
- opens only 22/80/443;
- adds 4 GB of swap;
- installs the DuckDNS updater;
- writes `.env`: random database, MinIO and de-identification secrets, the domain, and a session secret.

## 4 · Deploy

```bash
cd ~/phantom
bash scripts/deploy/deploy.sh --studies
```

The first run takes a while:
- It asks for the **admin password** (12+ characters). Only its hash is written to `.env`.
- It builds the image natively on ARM64, which takes 15–30 minutes the first time.
- It starts the stack and, because of `--studies`, loads the three public CT studies (head,
  chest, abdomen). That downloads the scans and the segmentation model; allow 20–40 minutes on
  the A1 cores.

Later updates are just `bash scripts/deploy/deploy.sh`: it runs `git pull`, then rebuilds and
restarts.

**Backups:** add this with `crontab -e`:

```
30 3 * * * bash /home/ubuntu/phantom/scripts/deploy/backup.sh >> /home/ubuntu/backup.log 2>&1
```

It takes a nightly Postgres dump plus silver and gold, and keeps 7 days in
`/var/backups/phantom`. Bronze is not copied: every scan here is public and can be fetched again.

**Uptime:** add a free UptimeRobot HTTP monitor on `https://<domain>/health`.

## 5 · Check it

From your own machine:

```bash
D=phantom-yourname.duckdns.org
curl -sI https://$D/ | head -1                               # 200
curl -s  https://$D/auth/me                                  # {"public":true,"admin":false}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://$D/demo/proofs/face   # 401
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/bronze/x             # 404
curl -s -o /dev/null -w '%{http_code}\n' https://files.$D/silver/x             # 404
nmap -Pn -p 1-10000 $D                                       # only 22, 80, 443 open
```

Then in a browser:
- open `/app`, a study, the hologram (the camera prompt appears: HTTPS) and the full body;
- sign in at `/app/login` and run one proof.

## Limits worth knowing

- **ARM64:** the image was built and tested on x86-64. On ARM every dependency has aarch64 wheels
  (checked with `pip download --platform manylinux2014_aarch64`), and S10's decimator was
  switched from pymeshlab (no ARM build) to fast-simplification for this reason. S8 timings on
  A1 cores are unmeasured until the first run.
- **One admin, no user accounts.** Out of scope by design.
- **Never upload real patient scans**, not even as the admin (CLAUDE.md hard rule 1).
- **Free tier:** Oracle can be out of A1 capacity, and can reclaim idle Always Free VMs unless
  the account is Pay As You Go.
