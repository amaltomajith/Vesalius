"""Loads the three public demo studies (head, chest, abdomen) into a running demo, in structures
mode, so the app's study library (/app/studies) has something to show on a fresh machine.

    docker compose exec -T api python scripts/load_demo_studies.py   # "Load 3D Studies.bat"

Every scan is public and licence-checked before use (CLAUDE.md hard rule 1):
  * head:    3D Slicer sample "CT-MR Brain", donated "to be used without any restrictions",
             fetched by its SHA256 and converted to DICOM (scripts/build_landing_assets.py);
  * chest:   TCIA LIDC-IDRI-0001, CC BY 3.0 (scripts/fetch_public_ct.py checks the licence);
  * abdomen: TCIA C4KC-KiTS-00002, CC BY 3.0 (same).
Each is uploaded through the same demo API a person uses (POST /demo/runs/upload, preset
"structures"), so every gate runs for real: the head is defaced at S7 before any surface exists.
Studies whose body part is already served by GET /studies are skipped, so re-running is cheap.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_landing_assets as head_src  # noqa: E402 -- sibling script
import fetch_public_ct as tcia  # noqa: E402 -- sibling script

API = "http://localhost:8000"
STUDIES = [  # (body part as served by /studies, declared body part, label)
    ("HEAD", "head", "head (3D Slicer CT-MR Brain)"),
    ("CHEST", "chest", "chest (TCIA LIDC-IDRI-0001)"),
    ("ABDOMEN", "abdomen", "abdomen (TCIA C4KC-KiTS-00002)"),
]
TCIA_LABEL = {"chest": "chest_lidc_0001", "abdomen": "abdomen_kits_00002"}


def _json(path: str) -> Any:
    with urllib.request.urlopen(API + path, timeout=120) as r:  # noqa: S310 -- local demo API
        return json.loads(r.read())


def _admin_cookie() -> dict[str, str]:
    """On a public deployment uploads are admin-only (src/api/auth.py). This script runs inside the
    api container, which holds PHANTOM_SESSION_SECRET, so it signs its own short admin session
    instead of needing the password. Locally (not public) no cookie is needed or sent."""
    from api import auth

    if not auth.public_mode():
        return {}
    return {"Cookie": f"{auth.COOKIE}={auth.make_session()}"}


def _upload(zip_bytes: bytes, body_part: str) -> str:
    boundary = "----loaddemostudies"
    parts: list[bytes] = []
    fields = (("acknowledged", "true"), ("preset", "structures"), ("body_part", body_part))
    for name, value in fields:
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(head.encode() + value.encode() + b"\r\n")
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="ct.zip"\r\n'
            "Content-Type: application/zip\r\n\r\n"
        ).encode()
        + zip_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(  # noqa: S310 -- local demo API
        API + "/demo/runs/upload",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **_admin_cookie()},
    )
    with urllib.request.urlopen(req, timeout=600) as r:  # noqa: S310
        return str(json.loads(r.read())["run_id"])


def _zip_for(body_part: str) -> bytes:
    if body_part == "head":
        zip_bytes, _info = head_src.to_dicom_zip(head_src.fetch())
        return zip_bytes
    label = TCIA_LABEL[body_part]
    collection, uid = tcia.SERIES[label]
    tcia.fetch(label, collection, uid)
    return (tcia.OUT / f"{label}.zip").read_bytes()


def _run(zip_bytes: bytes, body_part: str) -> bool:
    while _json("/demo/datasets")["busy"]:
        print("  the demo is busy with another run; waiting...", flush=True)
        time.sleep(10)
    run_id = _upload(zip_bytes, body_part)
    shown = ""
    started = time.time()
    while (run := _json(f"/demo/runs/{run_id}"))["state"] != "finished":
        current = next((s for s in run["steps"] if s["state"] == "running"), None)
        if current and current["title"] != shown:
            shown = current["title"]
            print(f"  {int(time.time() - started):>4}s  {shown}", flush=True)
        time.sleep(3)
    ok = bool(run["outcome"] == "success")
    print(f"  {'done' if ok else 'FAILED'} in {int(time.time() - started)}s: {run['message']}")
    return ok


def main() -> int:
    served = {s["body_part"] for s in _json("/studies")["studies"]}
    failures = 0
    for served_as, declared, label in STUDIES:
        if served_as in served:
            print(f"{label}: already loaded, skipping")
            continue
        print(f"{label}: downloading and checking its licence...", flush=True)
        try:
            zip_bytes = _zip_for(declared)
        except (SystemExit, OSError) as exc:
            print(f"  could not fetch it: {exc}")
            failures += 1
            continue
        print(f"  {len(zip_bytes) / 1e6:.0f} MB, running it through the pipeline (structures)")
        if not _run(zip_bytes, declared):
            failures += 1
    print()
    if failures:
        print(f"{failures} study(ies) did not load. The messages above say why.")
        return 1
    print("All demo studies are loaded. Open them at /app/studies on the demo's address.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
