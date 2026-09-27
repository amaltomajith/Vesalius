"""Fetches the public CT series used for phase-two structure-mode studies (chest, abdomen).

    python scripts/fetch_public_ct.py            # -> .cache/public_ct/<label>.zip + <label>.json

Source: The Cancer Imaging Archive (TCIA) public NBIA API (no login). Every series is checked
against its OWN licence as TCIA reports it, and refused unless it is a Creative Commons
Attribution licence without a NonCommercial / NoDerivatives term. The files are cached in the
gitignored .cache/ and never committed (CLAUDE.md hard rule 1: public sources only).

The head study is not here: it is the 3D Slicer "CT-MR Brain" sample, fetched and converted by
scripts/build_landing_assets.py.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import pydicom

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".cache" / "public_ct"
API = "https://services.cancerimagingarchive.net/nbia-api/services/v1"

# label -> (collection, SeriesInstanceUID). Chosen for coverage and a moderate download size.
SERIES: dict[str, tuple[str, str]] = {
    "chest_lidc_0001": (
        "LIDC-IDRI",
        "1.3.6.1.4.1.14519.5.2.1.6279.6001.179049373636438705059720603192",
    ),
    "abdomen_kits_00002": (
        "C4KC-KiTS",
        "1.3.6.1.4.1.14519.5.2.1.6919.4624.151906388809660735463831578045",
    ),
}


def _get(path: str, **params: str) -> bytes:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=600) as r:  # noqa: S310 -- fixed https API
        return bytes(r.read())


def licence_ok(name: str) -> bool:
    """Creative Commons Attribution (any version), with no NC or ND term."""
    n = name.lower()
    return (
        "creative commons attribution" in n
        and "noncommercial" not in n
        and "non-commercial" not in n
        and "noderiv" not in n
    )


def fetch(label: str, collection: str, series_uid: str) -> dict[str, Any]:
    meta = json.loads(_get("getSeries", SeriesInstanceUID=series_uid))
    if len(meta) != 1 or meta[0]["Collection"] != collection or meta[0]["Modality"] != "CT":
        raise SystemExit(f"{label}: series metadata does not match {collection} CT")
    m = meta[0]
    if not licence_ok(m.get("LicenseName", "")):
        raise SystemExit(f"{label}: licence not acceptable: {m.get('LicenseName')!r}")
    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / f"{label}.tcia.zip"  # as downloaded (TCIA adds a LICENSE text file)
    zip_path = OUT / f"{label}.zip"  # DICOM files only: what the pipeline ingests
    if not raw_path.exists():
        raw_path.write_bytes(_get("getImage", SeriesInstanceUID=series_uid))
    uids: set[str] = set()
    licence_text = ""
    files: list[str] = []
    out = zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(raw_path) as src, out as dst:
        for name in src.namelist():
            if name.endswith("/"):
                continue
            data = src.read(name)
            if name.rsplit("/", 1)[-1].upper().startswith("LICENSE"):
                licence_text = data.decode("utf-8", "replace").strip()
                continue
            ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True, force=True)
            if "SOPInstanceUID" not in ds:
                raise SystemExit(f"{label}: unexpected non-DICOM file {name!r} in the download")
            uids.add(str(ds.SeriesInstanceUID))
            files.append(name)
            dst.writestr(name.rsplit("/", 1)[-1], data)
    if uids != {series_uid}:
        raise SystemExit(
            f"{label}: zip holds {len(uids)} series, expected exactly the one requested"
        )
    provenance = {
        "label": label,
        "collection": collection,
        "collection_doi": m.get("CollectionURI"),
        "licence": m.get("LicenseName"),
        "licence_uri": m.get("LicenseURI"),
        "series_instance_uid": series_uid,
        "body_part": m.get("BodyPartExamined"),
        "images": len(files),
        "bytes": zip_path.stat().st_size,
        "tcia_licence_file": licence_text,
    }
    (OUT / f"{label}.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    return provenance


def main() -> None:
    wanted = sys.argv[1:] or list(SERIES)
    for label in wanted:
        collection, uid = SERIES[label]
        p = fetch(label, collection, uid)
        print(f"{label}: {p['images']} images, {p['bytes'] / 1e6:.0f} MB, {p['licence']}")


if __name__ == "__main__":
    main()
