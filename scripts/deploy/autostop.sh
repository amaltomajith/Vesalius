#!/usr/bin/env bash
# Puts the on-demand live backend to sleep: deallocates this Azure VM when nobody is using it, so
# the student credit is only spent while someone is (DEPLOY.md, part B). Run every minute by the
# systemd timer that `setup-server.sh ... --autostop` installs.
#
# Deallocate (not shut down: a VM that is only stopped is still billed) when:
#   - nothing but health checks reached the api for VESALIUS_IDLE_MINUTES (default 15), or
#   - the VM has been up VESALIUS_SESSION_MINUTES (default 60), or
#   - it has been up VESALIUS_HARD_MINUTES (default 180), even mid-job (a job that hangs).
# A scan or a proof in progress (the api's HEAVY_LOCK) is never cut short before the hard limit.
# Nor is maintenance: while <repo>/.autostop-hold exists (deploy.sh and setup-server.sh create it;
# `touch` it by hand to work on the server) the VM is kept, until the hold is older than the
# hard limit, so a forgotten hold cannot keep it running for ever.
#
# The VM deallocates itself through its managed identity (deploy/azure/provision.sh grants it
# exactly that), so no credential is stored on it.
#
#   bash scripts/deploy/autostop.sh                       # decide and act (as root, from systemd)
#   bash scripts/deploy/autostop.sh --decide UP IDLE BUSY # print the decision only (tests)
set -euo pipefail

IDLE=$(( ${VESALIUS_IDLE_MINUTES:-15} * 60 ))
SESSION=$(( ${VESALIUS_SESSION_MINUTES:-60} * 60 ))
HARD=$(( ${VESALIUS_HARD_MINUTES:-180} * 60 ))
IMDS="http://169.254.169.254/metadata"

# decide UPTIME_S IDLE_S BUSY(true|false) -> "keep" | "stop <reason>"
decide() {
  local up=$1 idle=$2 busy=$3
  if (( up >= HARD )); then echo "stop: up ${up}s, past the hard limit"; return; fi
  if [[ $busy == true ]]; then echo "keep: a scan or proof is running"; return; fi
  if (( up >= SESSION )); then echo "stop: up ${up}s, the session limit"; return; fi
  if (( idle >= IDLE )); then echo "stop: idle ${idle}s"; return; fi
  echo "keep: idle ${idle}s, up ${up}s"
}

# the api's view: {"idle_seconds": ..., "busy": ...}; unknown (still starting) counts as idle since boot
api_idle() {
  local repo
  repo="$(cd "$(dirname "$0")/../.." && pwd)"
  (cd "$repo" && docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T api \
    python -c 'import urllib.request as u; print(u.urlopen("http://127.0.0.1:8000/health/idle", timeout=5).read().decode())') \
    2>/dev/null || true
}

deallocate() {
  local token rid
  token="$(curl -fsS -H Metadata:true \
    "$IMDS/identity/oauth2/token?api-version=2018-02-01&resource=https://management.azure.com/" \
    | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])')"
  rid="$(curl -fsS -H Metadata:true "$IMDS/instance/compute/resourceId?api-version=2021-02-01&format=text")"
  curl -fsS -X POST -H "Authorization: Bearer $token" -H "Content-Length: 0" \
    "https://management.azure.com${rid}/deallocate?api-version=2024-07-01" >/dev/null
}

main() {
  local up idle busy json decision repo hold
  repo="$(cd "$(dirname "$0")/../.." && pwd)"
  hold="${VESALIUS_AUTOSTOP_HOLD:-$repo/.autostop-hold}"
  if [[ -f $hold ]] && (( $(date +%s) - $(stat -c %Y "$hold") < HARD )); then
    echo "autostop: keep: held by $hold"
    return
  fi
  up="${AUTOSTOP_UPTIME:-$(cut -d. -f1 /proc/uptime)}"  # the override is for tests
  json="$(api_idle)"
  if [[ -n $json ]]; then
    idle="$(printf '%s' "$json" | python3 -c 'import json, sys; print(int(json.load(sys.stdin)["idle_seconds"]))')"
    busy="$(printf '%s' "$json" | python3 -c 'import json, sys; print(str(json.load(sys.stdin)["busy"]).lower())')"
  else
    idle=$up busy=false
  fi
  decision="$(decide "$up" "$idle" "$busy")"
  echo "autostop: $decision"
  if [[ $decision == stop* ]]; then
    deallocate
  fi
}

if [[ ${1:-} == --decide ]]; then
  decide "$2" "$3" "$4"
else
  main
fi
