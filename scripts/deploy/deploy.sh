#!/usr/bin/env bash
# Deploy (or redeploy) the public Vesalius showcase on the server. Run from the repository:
#
#   bash scripts/deploy/deploy.sh            # pull the code and the CI-built image, restart, wait until healthy
#   bash scripts/deploy/deploy.sh --studies  # ... then load the three public demo studies
#   bash scripts/deploy/deploy.sh --build    # build the image here instead (slow on 2 cores; if CI is down)
#
# First run: asks for the admin password once and stores only its scrypt hash in .env.
set -euo pipefail
cd "$(dirname "$0")/../.."
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
env_value() { sed -n "s/^$1=//p" .env | head -n1; }

[ -f .env ] || { echo "No .env: run scripts/deploy/setup-server.sh first." >&2; exit 1; }
DOMAIN="$(env_value VESALIUS_DOMAIN)"
[ -n "$DOMAIN" ] || { echo "VESALIUS_DOMAIN is not set in .env." >&2; exit 1; }

echo "== pull"
git pull --ff-only

[ -n "$(env_value VESALIUS_IMAGE)" ] || { echo "VESALIUS_IMAGE is not set in .env." >&2; exit 1; }
IMAGE="$(env_value VESALIUS_IMAGE):$(env_value VESALIUS_TAG)"; IMAGE="${IMAGE%:}"
case " $* " in
  *" --build "*) echo "== build (here, on the server)"; docker build -t "$IMAGE" -f docker/api/Dockerfile . ;;
  *) echo "== pull $IMAGE"; docker pull "$IMAGE" ;;
esac

if [ -z "$(env_value VESALIUS_ADMIN_PASSWORD_HASH)" ]; then
  echo "== admin password (12+ characters; only its hash is stored)"
  # The base file only: the prod overlay refuses to start without the hash we are making. The
  # prompts go to the terminal; the hash goes to a file (never echoed, never in shell history).
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  # the pulled image directly: the base compose file would build one if it were missing
  docker run --rm -it -v "$TMP:/out" --entrypoint sh "$IMAGE" -c 'python -m api.auth hash > /out/hash'
  HASH="$(tr -d '\r\n' < "$TMP/hash")"
  case "$HASH" in scrypt:*) ;; *) echo "Could not create the password hash." >&2; exit 1 ;; esac
  sed -i '/^VESALIUS_ADMIN_PASSWORD_HASH=/d' .env
  printf 'VESALIUS_ADMIN_PASSWORD_HASH=%s\n' "$HASH" >> .env
fi

echo "== start"
"${COMPOSE[@]}" up -d --remove-orphans

echo "== wait for health"
for _ in $(seq 1 60); do
  if "${COMPOSE[@]}" exec -T api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" >/dev/null 2>&1; then
    echo "   api healthy"
    break
  fi
  sleep 5
done
"${COMPOSE[@]}" ps

if [[ " $* " == *" --studies "* ]]; then
  echo "== load the three public demo studies (15-25 minutes the first time)"
  "${COMPOSE[@]}" exec -T api python scripts/load_demo_studies.py
fi

echo
echo "Live at https://$DOMAIN/  (certificates can take a minute on the very first start)"
