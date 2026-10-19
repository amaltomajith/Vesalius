"""Export the served gold of every study, plus the last proof results, as a static site bundle.

The public site is static (GitHub Pages, DEPLOY.md): the app shows the three public CT studies, the
full-body atlas and the recorded proofs without any server. This builds the data half of that site
from a running API, reading ONLY the routes the viewer itself uses (the gold serving surface, never
bronze or silver; CLAUDE.md hard rule 3), and writes a tree that mirrors the site:

    <out>/app/data/studies.json                       index, one credit per study
    <out>/app/data/studies/<id>/structures.json       the gold manifest, byte for byte
    <out>/app/data/studies/<id>/mesh/<group>.glb      group meshes, checked against gold records
    <out>/app/data/studies/<id>/{lineage.json, thumb.png, CREDIT.txt}
    <out>/app/data/proofs/{last.json, defacing.png}   the last proof results
    <out>/app/data/datasets.json                      the catalogue, without live state
    <out>/demo/proofs/last                            the landing page reads proofs here

It fails closed and writes nothing when any study is unsafe or unverifiable:
- a manifest with deface.required and not applied (the viewer would refuse it too);
- a head study whose structures were not cut to S7's face region (s7_region_v1);
- a mesh whose size or sha256 matches no gold artifact record of its series;
- a study whose body part has no known public source (no credit = no licence to publish);
- proof 5 ("viewer": a head study in the viewer has no face) not passed in the last results.

    python scripts/export_static.py --api http://127.0.0.1:8000 --out site-data

Like every public scan it is never committed: CI publishes it (.github/workflows/pages.yml).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

Get = Callable[[str], bytes]  # absolute URL or API path -> body; raises on any non-2xx

HEAD_CUT = "s7_region_v1"
REQUIRED_PROOF = "viewer"  # proof 5: a head study read back through the viewer routes has no face

# every study published must name its public source; refs: LICENSES.md "Imaging data"
CREDITS: dict[str, str] = {
    "HEAD": (
        '3D Slicer sample data, "CT-MR Brain" (CT-brain.nrrd), donated to be used without any '
        "restrictions (Slicer SampleData.py). Face removed by this pipeline's S7 before meshing."
    ),
    "CHEST": (
        "The Cancer Imaging Archive (TCIA), LIDC-IDRI, patient LIDC-IDRI-0001, one chest CT "
        "series. CC BY 3.0, doi:10.7937/K9/TCIA.2015.LO9QL9SX. Armato SG III, et al."
    ),
    "ABDOMEN": (
        "The Cancer Imaging Archive (TCIA), C4KC-KiTS, patient KiTS-00002, one abdomen CT series. "
        "CC BY 3.0, doi:10.7937/TCIA.2019.IX49E8NX. Heller N, et al."
    ),
}
CHANGES = (
    "Changes: de-identified, segmented (TotalSegmentator `total`, Apache-2.0), meshed and exported "
    "by the Vesalius pipeline. series {series_id}, pipeline {pipeline_version}."
)


class RefusedError(Exception):
    """The bundle would publish something unsafe or unverifiable; nothing is written."""


def _json(get: Get, path: str) -> Any:
    return json.loads(get(path))


def build(get: Get, staging: Path) -> list[dict[str, Any]]:
    """Fetch and check everything into `staging`; return the study index. Raises RefusedError."""
    last = _json(get, "/demo/proofs/last")
    verdict = (last.get(REQUIRED_PROOF) or {}).get("verdict")
    if verdict != "passed":
        raise RefusedError(
            f"proof '{REQUIRED_PROOF}' must have passed before publishing (got {verdict!r})"
        )

    data = staging / "app" / "data"
    index: list[dict[str, Any]] = []
    for study in _json(get, "/studies")["studies"]:
        sid, part = study["id"], (study.get("body_part") or "").upper()
        if part not in CREDITS:
            raise RefusedError(f"study {sid}: body part {part!r} has no known public source")
        raw = get(f"/studies/{sid}/structures")
        manifest = json.loads(raw)
        deface = manifest.get("deface") or {}
        if deface.get("required") and not deface.get("applied"):
            raise RefusedError(f"study {sid}: face removal required but not applied")
        if part == "HEAD" and deface.get("structures_cut") != HEAD_CUT:
            raise RefusedError(f"study {sid}: head structures not cut to {HEAD_CUT}")

        gold = {
            (a["bytes"], a["sha256"]) for a in _json(get, f"/series/{sid}/artifacts")["artifacts"]
        }
        root = data / "studies" / sid
        (root / "mesh").mkdir(parents=True)
        (root / "structures.json").write_bytes(raw)
        for group in manifest["groups"]:
            body = get(f"/studies/{sid}/mesh/{group['id']}.glb")
            sha = hashlib.sha256(body).hexdigest()
            if len(body) != group["bytes"] or (len(body), sha) not in gold:
                raise RefusedError(f"study {sid}: mesh {group['id']} matches no gold artifact")
            target = root / group["file"]
            if target.parent != root / "mesh":  # the manifest names files inside mesh/ only
                raise RefusedError(f"study {sid}: unexpected mesh path {group['file']!r}")
            target.write_bytes(body)
        (root / "lineage.json").write_bytes(get(f"/studies/{sid}/lineage"))
        if study.get("thumbnail_artifact_id"):
            link = _json(get, f"/artifacts/{study['thumbnail_artifact_id']}")
            (root / "thumb.png").write_bytes(get(link["url"]))
        credit = CREDITS[part]
        (root / "CREDIT.txt").write_text(
            credit
            + "\n\n"
            + CHANGES.format(series_id=sid, pipeline_version=manifest["pipeline_version"])
            + "\n",
            encoding="utf-8",
        )
        index.append({**study, "credit": credit})

    proofs = data / "proofs"
    proofs.mkdir(parents=True)
    last_bytes = json.dumps(last, sort_keys=True, indent=1).encode()
    (proofs / "last.json").write_bytes(last_bytes)
    # the side-by-side image exists only after the defacing proof ran in that API process
    with contextlib.suppress(urllib.error.HTTPError, FileNotFoundError):
        (proofs / "defacing.png").write_bytes(get("/demo/proofs/image"))
    landing = staging / "demo" / "proofs"
    landing.mkdir(parents=True)
    (landing / "last").write_bytes(last_bytes)

    catalogue = _json(get, "/demo/datasets")
    static_catalogue = {
        "datasets": catalogue["datasets"],
        "proofs": catalogue["proofs"],
        "upload": catalogue["upload"],
        "busy": False,
        "latest_run": None,
    }
    (data / "datasets.json").write_text(
        json.dumps(static_catalogue, sort_keys=True, indent=1), encoding="utf-8"
    )
    (data / "studies.json").write_text(
        json.dumps({"studies": index}, sort_keys=True, indent=1), encoding="utf-8"
    )
    return index


def export(get: Get, out: Path) -> list[dict[str, Any]]:
    """Build into a temporary directory and only then replace `out`: a refusal writes nothing."""
    with tempfile.TemporaryDirectory() as tmp:
        index = build(get, Path(tmp))
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(tmp, out)
    return index


def http_getter(api: str) -> Get:
    def get(path: str) -> bytes:
        url = path if path.startswith(("http://", "https://")) else api.rstrip("/") + path
        with urllib.request.urlopen(url, timeout=120) as r:  # noqa: S310 -- follows presigned 307s
            return bytes(r.read())

    return get


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path, default=Path("site-data"))
    a = ap.parse_args()
    try:
        index = export(http_getter(a.api), a.out)
    except RefusedError as exc:
        print(f"refused, nothing written: {exc}", file=sys.stderr)
        return 1
    parts = ", ".join(s["body_part"] for s in index)
    print(f"exported {len(index)} studies ({parts}) and the proof results -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
