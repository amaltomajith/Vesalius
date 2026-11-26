"""Bundle one pipeline study into the viewer as its offline demo (refs/INTEGRATION.md §4.1).

The viewer keeps a demo study so it still runs without a pipeline instance. The demo is not
hand-made: it is the gold output (structures.json + group GLBs) of a public CC BY chest CT run
through this pipeline, pulled from a running API through the same serving routes the viewer uses.
Like every public scan in this repo it is fetched, never committed (viewer/public/demo/ is
gitignored); the attribution travels with it in CREDIT.txt.

    python scripts/bundle_viewer_demo.py [--api http://127.0.0.1:8001] [--series <uuid>]

Without --series the newest CHEST structures study listed by GET /studies is used.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "viewer" / "public" / "demo" / "chest"
CREDIT = """\
Bundled demo study for the Phantom viewer.

Source: The Cancer Imaging Archive (TCIA), LIDC-IDRI collection, patient LIDC-IDRI-0001,
one chest CT series. Licence: CC BY 3.0. DOI: 10.7937/K9/TCIA.2015.LO9QL9SX
Armato SG III, et al. The Lung Image Database Consortium (LIDC) and Image Database Resource
Initiative (IDRI). Data from The Cancer Imaging Archive.

Changes: de-identified, segmented (TotalSegmentator `total`, Apache-2.0), meshed and exported by
this pipeline. series {series_id}, pipeline {pipeline_version}.
"""


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # follows the presigned 307
        return bytes(r.read())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8001")
    ap.add_argument("--series")
    a = ap.parse_args()

    sid = a.series
    if not sid:
        studies = json.loads(_get(f"{a.api}/studies"))["studies"]
        chest = [s for s in studies if s.get("body_part") == "CHEST"]
        if not chest:
            print("no CHEST structures study served; run the chest CT in structures mode first")
            return 1
        sid = chest[0]["id"]

    manifest_bytes = _get(f"{a.api}/studies/{sid}/structures")
    manifest = json.loads(manifest_bytes)
    # the viewer refuses these anyway; refuse to bundle them in the first place
    deface = manifest["deface"]
    if deface["required"] and not deface["applied"]:
        print("refusing: undefaced head study")
        return 1

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "mesh").mkdir(parents=True)
    (OUT / "structures.json").write_bytes(manifest_bytes)
    total = 0
    for g in manifest["groups"]:
        body = _get(f"{a.api}/studies/{sid}/mesh/{g['id']}.glb")
        if len(body) != g["bytes"]:
            print(f"size mismatch for {g['id']}: {len(body)} != {g['bytes']}")
            return 1
        (OUT / g["file"]).write_bytes(body)
        total += len(body)
    (OUT / "CREDIT.txt").write_text(
        CREDIT.format(series_id=sid, pipeline_version=manifest["pipeline_version"]),
        encoding="utf-8",
    )
    print(f"bundled {sid}: {len(manifest['groups'])} groups, {total / 1e6:.1f} MB -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
