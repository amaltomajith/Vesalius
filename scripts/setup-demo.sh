#!/usr/bin/env bash
# First-run setup for the demo on macOS / Linux: the same job as scripts/setup-demo.ps1 (which
# "Start Demo.bat" runs on Windows). Idempotent: it only CREATES what is missing and never
# overwrites anything (changing a password after the database volume exists would lock the app
# out of its own database).
#
#   .env                                 passwords, MinIO keys, de-identification secrets, DEMO_PORT
#   secrets/postgres_superuser_password  must equal POSTGRES_SUPERUSER_PASSWORD in .env
#   secrets/minio_root_password          MinIO admin password
set -euo pipefail
cd "$(dirname "$0")/.."

secret() { od -An -tx1 -N"${1:-24}" /dev/urandom | tr -d ' \n'; }
env_value() { sed -n "s/^$1=//p" .env | tr -d '\r' | head -n1; }

mkdir -p secrets

if [ ! -f .env ]; then
  [ -f .env.example ] || { echo ".env.example is missing - the download looks incomplete." >&2; exit 1; }
  tr -d '\r' < .env.example > .env   # LF only: a trailing CR would end up inside the values
  # every changeme-* placeholder gets its own random value
  while grep -q 'changeme-[a-z-]*' .env; do
    ph=$(grep -o 'changeme-[a-z-]*' .env | head -n1)
    sed -i.bak "s/$ph/$(secret 12)/" .env
  done
  sed -i.bak "s/^DEID_UID_SECRET=.*/DEID_UID_SECRET=$(secret 32)/" .env
  sed -i.bak "s/^DEID_DATE_SHIFT_SECRET=.*/DEID_DATE_SHIFT_SECRET=$(secret 32)/" .env
  rm -f .env.bak
  echo "  created .env with fresh random passwords"
fi

for name in DEID_UID_SECRET DEID_DATE_SHIFT_SECRET; do
  if [ -z "$(env_value "$name")" ]; then
    if grep -q "^$name=" .env; then sed -i.bak "s/^$name=.*/$name=$(secret 32)/" .env; rm -f .env.bak
    else printf '\n%s=%s\n' "$name" "$(secret 32)" >> .env; fi
    echo "  filled in $name"
  fi
done

if [ -z "$(env_value DEMO_PORT)" ]; then
  chosen=""
  for p in 8000 8001 8080 8088 8123 8765 18000; do
    if ! (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then chosen=$p; break; fi
  done
  [ -n "$chosen" ] || { echo "No free port found for the demo page." >&2; exit 1; }
  printf '\nDEMO_PORT=%s\n' "$chosen" >> .env
  echo "  the demo page will use port $chosen"
fi

if [ ! -f secrets/postgres_superuser_password ]; then
  pw=$(env_value POSTGRES_SUPERUSER_PASSWORD)
  [ -n "$pw" ] || { echo "POSTGRES_SUPERUSER_PASSWORD is empty in .env" >&2; exit 1; }
  printf '%s' "$pw" > secrets/postgres_superuser_password
  echo "  created secrets/postgres_superuser_password"
fi
if [ ! -f secrets/minio_root_password ]; then
  printf '%s' "$(secret 16)" > secrets/minio_root_password
  echo "  created secrets/minio_root_password"
fi
echo "  setup complete: the demo will be at http://localhost:$(env_value DEMO_PORT)/"
