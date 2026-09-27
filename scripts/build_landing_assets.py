"""Builds the landing page's 3D scan (src/api/static/scan/) from a REAL, openly licensed head CT,
run through this pipeline end to end.

    python scripts/build_landing_assets.py [API_BASE]      # default http://localhost:8001

Source: 3D Slicer sample data "CT-MR Brain", file CT-brain.nrrd. Slicer's SampleData.py states:
"MRHead, CBCT-MR Head, and CT-MR Brain data sets were donated to 3D Slicer project by the persons
visible in the images, to be used without any restrictions." The file is fetched by its SHA256
(Slicer's own content address) and verified; it is cached in .cache/ and never committed.

Steps, each one checked:
 1. fetch + verify the NRRD;
 2. write it as a DICOM CT series (one file per slice, the NRRD's own oblique geometry: real
    ImageOrientationPatient / ImagePositionPatient / PixelSpacing, HU values unchanged);
 3. run it through the demo API twice (skin_surface, then bone) with body part HEAD, so S5's
    verification and S7's defacing gate apply for real; stop unless both runs succeed and the
    series records deface_required;
 4. download each run's GOLD surface.glb and manifest (gold keys are per series, so each is taken
    right after its own run), undo S11's per-mesh centring with the manifest's offset so skin and
    bone share one frame, and write a sagittal check render to .cache/ for a human look;
 5. sample a deterministic point cloud from the two DEFACED gold meshes, keeping each point's
    surface normal (so the page can light it), and write scan/head_points.bin + .json.

Only defaced gold output is used: no raw face of the donor is written anywhere.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library
import trimesh
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import UID, ExplicitVRLittleEndian, generate_uid

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"
OUT_DIR = ROOT / "src" / "api" / "static" / "scan"

SOURCE_URL = (
    "https://github.com/Slicer/SlicerTestingData/releases/download/SHA256/"
    "6a5b6caccb76576a863beb095e3bfb910c50ca78f4c9bf043aa42f976cfa53d1"
)
SOURCE_SHA256 = "6a5b6caccb76576a863beb095e3bfb910c50ca78f4c9bf043aa42f976cfa53d1"
CT_IMAGE_STORAGE = UID("1.2.840.10008.5.1.4.1.1.2")
RAS_TO_GLTF = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])  # as S11
MM_TO_M = 0.001
POINTS = {"skin": 100_000, "bone": 200_000}
SEED = 20260926


# ---- 1. fetch ----------------------------------------------------------------------------------


def fetch() -> Path:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / "CT-brain.nrrd"
    if not path.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=120) as r:  # noqa: S310 -- fixed https URL
            path.write_bytes(r.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        path.unlink()
        raise SystemExit(f"CT-brain.nrrd hash mismatch: {digest}")
    return path


# ---- 2. NRRD -> DICOM series ------------------------------------------------------------------


def to_dicom_zip(nrrd: Path) -> tuple[bytes, dict[str, Any]]:
    image = sitk.ReadImage(str(nrrd))  # ITK physical space is LPS, like DICOM
    hu = sitk.GetArrayFromImage(image)  # (k, j, i)
    if hu.min() < -32768 or hu.max() > 32767:
        raise SystemExit("HU values do not fit int16")
    direction = np.array(image.GetDirection()).reshape(3, 3)  # columns = i, j, k axes (LPS)
    row_dir, col_dir, slice_dir = direction[:, 0], direction[:, 1], direction[:, 2]
    sx, sy, sz = image.GetSpacing()
    origin = np.array(image.GetOrigin())
    study, series, frame = generate_uid(), generate_uid(), generate_uid()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for k in range(hu.shape[0]):
            sop = generate_uid()
            meta = FileMetaDataset()
            meta.MediaStorageSOPClassUID = CT_IMAGE_STORAGE
            meta.MediaStorageSOPInstanceUID = sop
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.ImplementationClassUID = generate_uid()
            ds = Dataset()
            ds.file_meta = meta
            ds.SOPClassUID, ds.SOPInstanceUID = CT_IMAGE_STORAGE, sop
            ds.StudyInstanceUID, ds.SeriesInstanceUID = study, series
            ds.FrameOfReferenceUID = frame
            ds.Modality = "CT"
            ds.BodyPartExamined = "HEAD"
            ds.StudyDescription = ds.SeriesDescription = "HEAD CT (3D Slicer sample CT-MR Brain)"
            ds.PatientName, ds.PatientID, ds.PatientSex = "ANON", "SLICER-CTBRAIN", "O"
            ds.InstanceNumber = k + 1
            ds.Rows, ds.Columns = int(hu.shape[1]), int(hu.shape[2])
            ds.PixelSpacing = [float(sy), float(sx)]  # [row spacing, column spacing]
            ds.SliceThickness = float(sz)
            ds.ImageOrientationPatient = [float(v) for v in (*row_dir, *col_dir)]
            ds.ImagePositionPatient = [float(v) for v in origin + k * sz * slice_dir]
            ds.RescaleSlope, ds.RescaleIntercept = 1.0, 0.0
            ds.PhotometricInterpretation, ds.SamplesPerPixel = "MONOCHROME2", 1
            ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 16, 15, 1
            ds.BurnedInAnnotation = "NO"
            ds.PixelData = hu[k].astype("<i2").tobytes()
            out = io.BytesIO()
            ds.save_as(out, enforce_file_format=True)
            zf.writestr(f"ct_{k:03d}.dcm", out.getvalue())
    geometry = {
        # slice normal and origin in RAS (flip L,P) for the story's "slices" layout
        "slice_normal_ras": [float(-slice_dir[0]), float(-slice_dir[1]), float(slice_dir[2])],
        "origin_ras": [float(-origin[0]), float(-origin[1]), float(origin[2])],
        "slice_spacing_mm": float(sz),
    }
    return buf.getvalue(), geometry


# ---- 3./4. run through the pipeline, collect gold ----------------------------------------------


def _get(base: str, path: str) -> bytes:
    with urllib.request.urlopen(base + path, timeout=120) as r:  # noqa: S310 -- local demo API
        return bytes(r.read())


def _json(base: str, path: str) -> Any:
    return json.loads(_get(base, path))


def _upload(base: str, zip_bytes: bytes, preset: str) -> str:
    boundary = "----landingassets" + preset.replace("_", "")
    parts: list[bytes] = []
    for name, value in (("acknowledged", "true"), ("preset", preset), ("body_part", "head")):
        head = f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
        parts.append(head.encode() + value.encode() + b"\r\n")
    file_head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="ct.zip"\r\n'
        "Content-Type: application/zip\r\n\r\n"
    )
    parts.append(file_head.encode() + zip_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(  # noqa: S310 -- local demo API
        base + "/demo/runs/upload",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:  # noqa: S310
        return str(json.loads(r.read())["run_id"])


def run_preset(base: str, zip_bytes: bytes, preset: str) -> tuple[trimesh.Trimesh, dict[str, Any]]:
    while _json(base, "/demo/datasets")["busy"]:
        time.sleep(5)
    run_id = _upload(base, zip_bytes, preset)
    while (run := _json(base, f"/demo/runs/{run_id}"))["state"] != "finished":
        time.sleep(3)
    if run["outcome"] != "success":
        raise SystemExit(f"{preset}: pipeline run {run['outcome']}: {run['message']}")
    sid = run["series_ids"][0]
    series = _json(base, f"/series/{sid}")
    if not series["deface_required"]:
        raise SystemExit(f"{preset}: series does not record deface_required; refusing to continue")
    arts = {a["kind"]: a for a in _json(base, f"/series/{sid}/artifacts")["artifacts"]}
    glb = _get(base, f"/demo/files/{arts['surface_glb']['id']}")
    manifest = json.loads(_get(base, f"/demo/files/{arts['manifest']['id']}"))
    (CACHE / f"gold_{preset}.glb").write_bytes(glb)
    loaded = trimesh.load(io.BytesIO(glb), file_type="glb", force="mesh")
    assert isinstance(loaded, trimesh.Trimesh)
    mesh = loaded
    offset = np.array(manifest["geometry"]["centring_offset_mm"])
    # undo S11: gltf_m = (ras + offset) @ R.T * 0.001  ->  ras = (gltf_m / 0.001) @ R - offset
    ras = (np.asarray(mesh.vertices) / MM_TO_M) @ RAS_TO_GLTF - offset
    mesh = trimesh.Trimesh(vertices=ras, faces=mesh.faces, process=False)
    print(f"{preset}: run {run_id} success, series {sid}, {len(mesh.faces)} faces")
    return mesh, {"run_id": run_id, "series_id": sid, "contract": series["contract_version"]}


def check_render(skin: trimesh.Trimesh, bone: trimesh.Trimesh) -> Path:
    """A sagittal projection (anterior right) of the DEFACED gold meshes, for a human look."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (640, 520), "white")
    d = ImageDraw.Draw(img)
    for mesh, colour in ((skin, (150, 190, 175)), (bone, (60, 60, 60))):
        v = np.asarray(mesh.vertices)
        centre = (skin.bounds[0] + skin.bounds[1]) / 2.0
        for y, z in (v[:: max(1, len(v) // 60000)] - centre)[:, 1:3]:
            d.point((320 + y * 2, 260 - z * 2), fill=colour)
    path = CACHE / "defaced_profile_check.png"
    img.save(path)
    return path


# ---- 5. point cloud -----------------------------------------------------------------------------


def sample_points(
    skin: trimesh.Trimesh, bone: trimesh.Trimesh, geometry: dict[str, Any]
) -> tuple[bytes, dict[str, Any]]:
    rng = np.random.default_rng(SEED)
    centre = (skin.bounds[0] + skin.bounds[1]) / 2.0  # one shared centre (RAS mm)
    normal = np.array(geometry["slice_normal_ras"])
    origin = np.array(geometry["origin_ras"])
    positions: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    tissues: list[np.ndarray] = []
    slices: list[np.ndarray] = []
    for tissue_id, (name, mesh) in enumerate((("skin", skin), ("bone", bone))):
        seed = int(rng.integers(1 << 31))
        pts, face_idx = trimesh.sample.sample_surface(mesh, POINTS[name], seed=seed)
        nrm = np.asarray(mesh.face_normals)[face_idx]
        # winding decides the sign; a closed-ish surface's normals point away from its centre
        outward = float(np.mean(np.sum(nrm * (pts - pts.mean(axis=0)), axis=1)))
        if outward < 0:
            nrm = -nrm
        verdict = "flipped" if outward < 0 else "kept"
        print(f"{name}: normals {verdict} (mean outward {outward:+.2f})")
        slices.append(np.rint(((pts - origin) @ normal) / geometry["slice_spacing_mm"]))
        positions.append(((pts - centre) @ RAS_TO_GLTF.T) * MM_TO_M)
        normals.append(nrm @ RAS_TO_GLTF.T)
        tissues.append(np.full(len(pts), tissue_id, np.uint8))
    pos = np.concatenate(positions).astype(np.float32)
    nrm_all = np.concatenate(normals)
    nrm_all /= np.maximum(np.linalg.norm(nrm_all, axis=1, keepdims=True), 1e-9)
    tissue = np.concatenate(tissues)
    slice_idx = np.concatenate(slices)
    slice_idx = (slice_idx - slice_idx.min()).astype(np.uint16)
    extent = float(np.abs(pos).max())
    q = np.rint(pos / extent * 32767).astype("<i2")
    qn = np.rint(nrm_all * 127).astype("i1")
    blob = q.tobytes() + qn.tobytes() + tissue.tobytes() + slice_idx.astype("<u2").tobytes()
    header = {
        "count": int(len(pos)),
        "counts": dict(POINTS),
        "layout": "int16 xyz[count] (x extent_m / 32767) | int8 normal xyz[count] (/ 127)"
        " | uint8 tissue[count] (0 skin, 1 bone) | uint16 slice[count]",
        "bytes_per_point": 12,
        "extent_m": extent,
        "slices": int(slice_idx.max()) + 1,
        "slice_normal_gltf": [float(x) for x in RAS_TO_GLTF @ normal],
        "bytes": len(blob),
    }
    return blob, header


def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001"
    nrrd = fetch()
    zip_bytes, geometry = to_dicom_zip(nrrd)
    (CACHE / "ct-brain-dicom.zip").write_bytes(zip_bytes)
    skin, skin_run = run_preset(base, zip_bytes, "skin_surface")
    bone, bone_run = run_preset(base, zip_bytes, "bone")
    print("check render:", check_render(skin, bone))
    blob, header = sample_points(skin, bone, geometry)
    header["provenance"] = {
        "source": "3D Slicer sample data CT-MR Brain, CT-brain.nrrd",
        "source_sha256": SOURCE_SHA256,
        "runs": {"skin_surface": skin_run, "bone": bone_run},
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "head_points.bin").write_bytes(blob)
    (OUT_DIR / "head_points.json").write_text(json.dumps(header, indent=2, sort_keys=True) + "\n")
    print(f"points: {header['count']}, {len(blob) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
