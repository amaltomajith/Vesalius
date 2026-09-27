#!/usr/bin/env bash
# Nightly backup on the server: a Postgres dump plus a copy of the silver and gold buckets, kept
# for 7 days under $BACKUP_DIR (default /var/backups/phantom). Bronze is deliberately NOT copied:
# it holds the PHI-bearing originals, and every scan here is public and can be fetched again.
#
#   crontab -e   ->   30 3 * * *  bash /home/ubuntu/phantom/scripts/deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
BACKUP_DIR="${BACKUP_DIR:-/var/backups/phantom}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/$STAMP"
mkdir -p "$OUT"
chmod 700 "$BACKUP_DIR"

"${COMPOSE[@]}" exec -T postgres pg_dump -U postgres -d pipeline -Fc > "$OUT/pipeline.dump"

# mc inside the minio container, with the root credentials it already has
for bucket in silver gold; do
  "${COMPOSE[@]}" exec -T minio sh -c \
    'mc alias set local http://localhost:9000 minioadmin "$(cat /run/secrets/minio_root_password)" >/dev/null && mc mirror --quiet local/'"$bucket"' /tmp/backup-'"$bucket"
  "${COMPOSE[@]}" cp "minio:/tmp/backup-$bucket" "$OUT/$bucket"   # the minio image has no tar
  "${COMPOSE[@]}" exec -T minio rm -rf "/tmp/backup-$bucket"
done

find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +7 -exec rm -rf {} +
echo "backup written to $OUT ($(du -sh "$OUT" | cut -f1))"
