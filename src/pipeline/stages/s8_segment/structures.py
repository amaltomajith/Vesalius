"""S8 structures mode -- the segmentation engine boundary and the per-structure measurements.

Two halves, deliberately separate:

* `SegmentationEngine` is the only place that knows about the model. The real implementation
  (`TotalSegmentatorEngine`) imports torch/TotalSegmentator lazily, inside its methods, so this
  module imports on any machine and unit tests inject a fake engine. It refuses to run unless the
  installed version is the pinned one and the model's usage statistics are switched off (a
  governed pipeline does not send telemetry about the scans it processes).
* `measure_structures` is pure: labels + HU volume + affine -> per-structure volume, HU mean/std
  and world-space bounding box (refs/INTEGRATION.md §3: "Compute them in S8 from the mask and the
  volume, before meshing, where they are cheap and exact").
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library
from scipy import ndimage

from pipeline.common.errors import SegmentationEngineError
from pipeline.stages.s8_segment import structures_params as sp

# the fast `total` model TotalSegmentator uses (task id 297); its files are hashed for provenance
_FAST_TOTAL_DATASET = "Dataset297_TotalSegmentator_total_3mm_1559subj"
_HASHED_FILES = ("plans.json", "fold_0/checkpoint_final.pth")


@dataclass(frozen=True)
class EngineOutput:
    labels: np.ndarray  # (k, j, i) integer label ids, same shape as the input volume
    names: dict[int, str]  # label id -> label name (the node-name vocabulary)
    # the label volume's own grid as read back from the engine's file: origin, spacing,
    # direction (ITK/LPS convention) -- compared against the gated volume by the service
    geometry: tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...]]


class SegmentationEngine(Protocol):
    def describe(self) -> dict[str, object]:
        """Engine identity recorded in the idempotency key: name, version, weights hash."""
        ...

    def segment(self, volume_nii_gz: bytes) -> EngineOutput: ...


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class TotalSegmentatorEngine:
    """The real engine. CPU only, `total` task only, fast (3 mm) model."""

    home_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TOTALSEG_HOME_DIR", "/weights"))
    )

    def _disable_usage_stats(self) -> None:
        cfg_path = self.home_dir / "config.json"
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        if cfg.get("send_usage_stats") is not False:
            cfg["send_usage_stats"] = False
            self.home_dir.mkdir(parents=True, exist_ok=True)
            cfg_path.write_text(json.dumps(cfg, indent=2))
        if json.loads(cfg_path.read_text()).get("send_usage_stats") is not False:
            raise SegmentationEngineError("could not switch TotalSegmentator usage statistics off")

    def _weights_dir(self) -> Path:
        return (
            self.home_dir
            / "nnunet"
            / "results"
            / _FAST_TOTAL_DATASET
            / "nnUNetTrainer_4000epochs_NoMirroring__nnUNetPlans__3d_fullres"
        )

    def describe(self) -> dict[str, object]:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("TotalSegmentator")
        except PackageNotFoundError as exc:
            raise SegmentationEngineError("TotalSegmentator is not installed") from exc
        if installed != sp.ENGINE_VERSION:
            raise SegmentationEngineError(
                f"TotalSegmentator {installed} installed, {sp.ENGINE_VERSION} pinned"
            )
        self._disable_usage_stats()
        wdir = self._weights_dir()
        if not all((wdir / f).exists() for f in _HASHED_FILES):
            from totalsegmentator.libs import download_pretrained_weights

            download_pretrained_weights(297)
        missing = [f for f in _HASHED_FILES if not (wdir / f).exists()]
        if missing:
            raise SegmentationEngineError(f"model weights missing after download: {missing}")
        weights = hashlib.sha256()
        for f in _HASHED_FILES:
            weights.update(f.encode())
            weights.update(_sha256_file(wdir / f).encode())
        return {
            "engine": sp.ENGINE,
            "engine_version": installed,
            "task": sp.TASK,
            "fast": sp.FAST,
            "weights_sha256": weights.hexdigest(),
        }

    def segment(self, volume_nii_gz: bytes) -> EngineOutput:
        self._disable_usage_stats()
        import nibabel as nib
        from totalsegmentator.map_to_binary import class_map
        from totalsegmentator.python_api import totalsegmentator

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "input.nii.gz"
            src.write_bytes(volume_nii_gz)
            out = totalsegmentator(
                input=nib.load(str(src)),
                output=None,
                ml=True,
                fast=sp.FAST,
                task=sp.TASK,
                device="cpu",
                quiet=True,
                nr_thr_saving=1,
            )
            dst = Path(tmp) / "labels.nii.gz"
            nib.save(out, str(dst))
            image = sitk.ReadImage(str(dst))
            labels = sitk.GetArrayFromImage(image)  # (k, j, i), as S6 wrote it
            geometry = (image.GetOrigin(), image.GetSpacing(), image.GetDirection())
        names = {int(k): str(v) for k, v in class_map[sp.TASK].items()}
        return EngineOutput(
            labels=np.asarray(labels).astype(np.int32), names=names, geometry=geometry
        )


# ---- face cut (structures mode only) ----------------------------------------------------------


def cut_face_region(
    labels: np.ndarray, affine_ras: np.ndarray, a_min: float, s_min: float
) -> tuple[np.ndarray, dict[int, int]]:
    """Clear every labelled voxel inside S7's face region -- RAS anterior >= a_min AND superior
    >= s_min -- so no structure (bone included) keeps the facial skeleton. Returns the cut labels
    and the voxels removed per label id. Never mutates `labels`."""
    out = labels.copy()
    kk, jj, ii = np.nonzero(out)
    if len(kk) == 0:
        return out, {}
    ijk = np.stack([ii, jj, kk], axis=1).astype(np.float64)  # affine columns are (i, j, k)
    world = ijk @ affine_ras[:3, :3].T + affine_ras[:3, 3]
    inside = (world[:, 1] >= a_min) & (world[:, 2] >= s_min)
    removed: dict[int, int] = {}
    if inside.any():
        ids, counts = np.unique(out[kk[inside], jj[inside], ii[inside]], return_counts=True)
        removed = {int(i): int(c) for i, c in zip(ids, counts, strict=True)}
        out[kk[inside], jj[inside], ii[inside]] = 0
    return out, removed


# ---- measurements -------------------------------------------------------------------------------


def _world_bbox(sl: tuple[slice, slice, slice], affine_ras: np.ndarray) -> list[list[float]]:
    """World-space (RAS mm) axis-aligned box around a voxel-index box. Index order is (k, j, i);
    the affine maps (i, j, k), so columns are reversed before the multiply (S6/S7 convention)."""
    ks, js, is_ = sl
    corners = np.array(
        [
            [i, j, k]
            for k in (ks.start, ks.stop - 1)
            for j in (js.start, js.stop - 1)
            for i in (is_.start, is_.stop - 1)
        ],
        dtype=float,
    )
    world = corners @ affine_ras[:3, :3].T + affine_ras[:3, 3]
    return [
        [round(float(v), 2) for v in world.min(axis=0)],
        [round(float(v), 2) for v in world.max(axis=0)],
    ]


def measure_structures(
    labels: np.ndarray, hu: np.ndarray, affine_ras: np.ndarray, names: dict[int, str]
) -> list[dict[str, object]]:
    """One record per label id present in `labels` (0 is background). Labels the engine emits but
    has no name for are kept as `label_<id>` -- never dropped."""
    if labels.shape != hu.shape:
        raise ValueError(f"labels {labels.shape} and volume {hu.shape} differ")
    voxel_ml = abs(float(np.linalg.det(affine_ras[:3, :3]))) / 1000.0
    boxes = ndimage.find_objects(labels)
    out: list[dict[str, object]] = []
    for idx, sl in enumerate(boxes, start=1):
        if sl is None:
            continue
        sel = labels[sl] == idx
        n = int(sel.sum())
        if n == 0:
            continue
        values = hu[sl][sel].astype(np.float64)
        name = names.get(idx, f"label_{idx}")
        out.append(
            {
                "label_id": idx,
                "node": name,
                "label": sp.display_label(name),
                "group": sp.group_for(name),
                "voxels": n,
                "volume_ml": round(n * voxel_ml, 2),
                "hu_mean": round(float(values.mean()), 2),
                "hu_std": round(float(values.std()), 2),
                "bbox_mm": _world_bbox(sl, affine_ras),
            }
        )
    return out
