#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 server (Oracle Cloud Always Free Ampere A1, ARM64) for
# the public Phantom showcase. Run it from the cloned repository, as a sudo-capable user:
#
#   sudo bash scripts/deploy/setup-server.sh phantom-yourname.duckdns.org <duckdns-token>
#
# Idempotent: safe to run again. It never overwrites an existing .env value or secret file.
#   1. Docker Engine + the Compose plugin (Docker's own apt repository)
#   2. firewall: only 22, 80 and 443 in (Oracle's Ubuntu images ship iptables rules that reject
#      everything else; the VCN security list must ALSO allow 80/443, see DEPLOY.md)
#   3. 4 GB swap (S8 peaks around 8 GB; swap is a cushion, not a plan)
#   4. DuckDNS updater every 5 minutes
#   5. .env: database/MinIO/de-identification secrets (scripts/setup-demo.sh), the domain and a
#      session secret. The admin password is set by scripts/deploy/deploy.sh on its first run.
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
apt-get install -y iptables-persistent >/dev/null
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT 2>/dev/null \
    || iptables -I INPUT 5 -p tcp --dport "$port" -m conntrack --ctstate NEW -j ACCEPT
done
iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p udp --dport 443 -j ACCEPT
netfilter-persistent save

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
env_set PHANTOM_DOMAIN "$DOMAIN"
env_set PHANTOM_SESSION_SECRET "$(od -An -tx1 -N32 /dev/urandom | tr -d ' \n')"
chown "$OWNER" .env && chmod 600 .env
chmod 700 secrets

echo
echo "Done. Log out and back in (docker group), then run:  bash scripts/deploy/deploy.sh"
