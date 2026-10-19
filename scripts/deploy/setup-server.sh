#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 ARM64 server (Azure B2ps_v2 / B2pls_v2, or Oracle's A1) for
# the public Vesalius showcase. Run it from the cloned repository, as a sudo-capable user:
#
#   sudo bash scripts/deploy/setup-server.sh vesalius-yourname.duckdns.org <duckdns-token>
#
# Idempotent: safe to run again. It never overwrites an existing .env value or secret file.
#   1. Docker Engine + the Compose plugin (Docker's own apt repository)
#   2. firewall: the cloud's own (Azure network security group, Oracle security list) must allow
#      22/80/443, see DEPLOY.md. Oracle's Ubuntu images ALSO ship host iptables rules that reject
#      everything else, so 80/443 are opened there, ahead of the first REJECT; Azure's have none.
#   3. 4 GB swap (S8 peaks around 8 GB; swap is a cushion, not a plan)
#   4. DuckDNS updater every 5 minutes
#   5. .env: database/MinIO/de-identification secrets (scripts/setup-demo.sh), the domain, a
#      session secret and the CI image name. The admin password is set by scripts/deploy/deploy.sh
#      on its first run; the Groq key, if any, you add yourself.
#   6. automatic security updates
set -euo pipefail

DOMAIN="${1:?usage: setup-server.sh <name>.duckdns.org <duckdns-token>}"
TOKEN="${2:?usage: setup-server.sh <name>.duckdns.org <duckdns-token>}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OWNER="${SUDO_USER:-$(id -un)}"
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo." >&2; exit 1; }
case "$DOMAIN" in *.duckdns.org) ;; *) echo "Expected <name>.duckdns.org, got $DOMAIN" >&2; exit 1 ;; esac

echo "== 1/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
usermod -aG docker "$OWNER"
systemctl enable --now docker
docker compose version

echo "== 2/5 Firewall (22, 80, 443 only)"
# the first REJECT rule in INPUT, if the image ships one (Oracle); Azure's images have none
REJECT_AT="$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')"
if [ -n "$REJECT_AT" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null
  for port in 80 443; do
    iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null \
      || iptables -I INPUT "$REJECT_AT" -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
  done
  iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null \
    || iptables -I INPUT "$REJECT_AT" -p udp --dport 443 -j ACCEPT
  netfilter-persistent save
else
  echo "   no host REJECT rules: the cloud's network security group decides (DEPLOY.md)"
fi

echo "== 3/5 Swap"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 4/5 DuckDNS updater"
SUB="${DOMAIN%.duckdns.org}"
install -m 600 /dev/null /etc/duckdns.env
printf 'SUB=%s\nTOKEN=%s\n' "$SUB" "$TOKEN" > /etc/duckdns.env
cat > /usr/local/bin/duckdns-update <<'EOF'
#!/bin/sh
. /etc/duckdns.env
curl -fsS "https://www.duckdns.org/update?domains=${SUB}&token=${TOKEN}&ip=" -o /var/log/duckdns.last
EOF
chmod 700 /usr/local/bin/duckdns-update
echo '*/5 * * * * root /usr/local/bin/duckdns-update' > /etc/cron.d/duckdns
/usr/local/bin/duckdns-update
echo "   DuckDNS says: $(cat /var/log/duckdns.last) (OK means $DOMAIN points here)"

echo "== 5/5 .env"
cd "$REPO"
sudo -u "$OWNER" bash scripts/setup-demo.sh
env_set() {  # adds KEY=VALUE only if KEY has no value yet
  if ! grep -q "^$1=." .env; then
    sed -i "/^$1=/d" .env
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
env_set VESALIUS_DOMAIN "$DOMAIN"
env_set VESALIUS_SESSION_SECRET "$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
# the api image CI publishes for this repository: ghcr.io/<GitHub owner>/vesalius-api
# as the repository owner: under sudo, git refuses a repository owned by someone else
ORIGIN="$(sudo -u "$OWNER" git -C "$REPO" remote get-url origin 2>/dev/null || true)"
GH_OWNER="$(printf '%s' "$ORIGIN" | sed -nE 's#.*github\.com[:/]([^/]+)/.*#\1#p' | tr '[:upper:]' '[:lower:]')"
if [ -n "$GH_OWNER" ]; then env_set VESALIUS_IMAGE "ghcr.io/$GH_OWNER/vesalius-api"
else echo "   (could not read the GitHub owner from the git remote: set VESALIUS_IMAGE in .env)"; fi
chown "$OWNER" .env && chmod 600 .env
chmod 700 secrets

echo
echo "== automatic security updates"
apt-get install -y unattended-upgrades >/dev/null
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'APT'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT

echo "Done. Log out and back in (docker group), then run:  bash scripts/deploy/deploy.sh"
