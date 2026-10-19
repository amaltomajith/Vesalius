#!/usr/bin/env bash
# Assembles the static site (GitHub Pages, DEPLOY.md part A) from a built app and exported data.
# Used by .github/workflows/pages.yml and for a local rehearsal; the layout mirrors the API's:
#
#   <out>/index.html        the landing page (data-static: hides what needs the API)
#   <out>/static/           its assets (src/api/static)
#   <out>/app/              the app, built with `vite build --mode pages` (viewer/dist)
#   <out>/app/atlas/        the full-body atlas (assets/atlas)
#   <out>/app/data/, <out>/demo/proofs/last   the exported studies and proofs (export_static.py)
#   <out>/app/config.json   {wakeUrl, backendUrl} from WAKE_URL / BACKEND_URL (empty: no live backend)
#   <out>/404.html          the app again, so deep links (/app/hologram/...) survive a refresh
#
#   bash scripts/build_pages_site.sh <out> [<exported data dir>]
set -euo pipefail

OUT="${1:?usage: build_pages_site.sh <out> [<exported data dir>]}"
DATA="${2:-}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
[ -f "$REPO/viewer/dist/index.html" ] || { echo "build the app first: npm run build -- --mode pages" >&2; exit 1; }
if grep -q 'src="/app/assets/' "$REPO/viewer/dist/index.html"; then  # built for the API, not Pages
  echo "viewer/dist was built for the API (/app/): build it with --mode pages" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT/static" "$OUT/app"
cp -r "$REPO/src/api/static/." "$OUT/static/"
sed 's/<html lang="en">/<html lang="en" data-static>/' "$REPO/src/api/static/landing.html" > "$OUT/index.html"
grep -q 'data-static' "$OUT/index.html" || { echo "landing.html: no <html lang=\"en\"> to mark" >&2; exit 1; }
cp -r "$REPO/viewer/dist/." "$OUT/app/"
cp -r "$REPO/assets/atlas" "$OUT/app/atlas"
cp "$OUT/app/index.html" "$OUT/404.html"

if [ -n "$DATA" ]; then
  cp -r "$DATA/." "$OUT/"
else
  echo "warning: no exported data: the site will list no studies" >&2
fi

WAKE_URL="${WAKE_URL:-}" BACKEND_URL="${BACKEND_URL:-}" python3 -c '
import json, os
print(json.dumps({"wakeUrl": os.environ["WAKE_URL"] or None, "backendUrl": os.environ["BACKEND_URL"] or None}))
' > "$OUT/app/config.json"
touch "$OUT/.nojekyll"

big="$(find "$OUT" -type f -size +95M)"
[ -z "$big" ] || { echo "over the 100 MB per-file limit: $big" >&2; exit 1; }
total="$(du -sm "$OUT" | cut -f1)"
[ "$total" -le 950 ] || { echo "the site is ${total} MB (limit 1 GB)" >&2; exit 1; }
echo "site: ${total} MB -> $OUT"
