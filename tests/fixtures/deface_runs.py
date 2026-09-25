"""Cached phantom renders and defacing runs. Rendering a phantom and defacing it costs seconds per
orientation; the unit tests only READ them, so they are computed once per process and frozen
(read-only arrays) to make an accidental in-place mutation fail loudly instead of leaking between
tests."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from functools import cache
from typing import Any
from uuid import UUID, uuid4

import numpy as np
import SimpleITK as sitk  # noqa: N813

from pipeline.catalog.models import Artifact, Series, TransformRun
from pipeline.common.hashing import sha256_of_bytes
from pipeline.stages.s6_volume_assembly.affine import LPS_TO_RAS, read_nifti_sform
from pipeline.stages.s6_volume_assembly.meta import build_meta_json
from pipeline.stages.s7_deface.algorithm import DefaceResult, deface_region
from pipeline.stages.s7_deface.service import S6Inputs
from tests.fixtures import head_phantom as hp
from tests.fixtures.volume_asserts import StubSilverClient

AIR_VALUE = -1000.0


def freeze(*arrays: np.ndarray) -> None:
    for a in arrays:
        a.flags.writeable = False


@cache
def phantom(name: str, shoulders: bool = False) -> hp.Phantom:
    ph = hp.render_variant(name, shoulders=shoulders)
    freeze(ph.volume, ph.grid.affine_ras, *ph.truth.values())
    return ph


@cache
def defaced(name: str) -> DefaceResult:
    ph = phantom(name)
    res = deface_region(ph.volume, ph.grid.affine_ras, air_value=AIR_VALUE)
    freeze(res.defaced, res.region_mask, res.changed_mask)
    return res


def world_axis(grid: hp.Grid, axis: int) -> np.ndarray:
    """World coordinate `axis` (0=x, 1=y anterior, 2=z superior) of EVERY voxel, computed here
    from the raw affine in native (i, j, k) order -- independent of S7's code."""
    nk, nj, ni = grid.shape
    a = grid.affine_ras
    k, j, i = np.meshgrid(
        np.arange(nk, dtype=np.float64),
        np.arange(nj, dtype=np.float64),
        np.arange(ni, dtype=np.float64),
        indexing="ij",
    )
    return np.asarray(a[axis, 0] * i + a[axis, 1] * j + a[axis, 2] * k + a[axis, 3])


def index_of(grid: hp.Grid, world_xyz: tuple[float, float, float]) -> tuple[int, int, int]:
    """(k, j, i) array index of the voxel nearest a RAS point."""
    ijk = np.linalg.solve(grid.affine_ras, np.array([*world_xyz, 1.0]))[:3]
    i, j, k = (int(round(float(v))) for v in ijk)
    return k, j, i


# ---------------------------------------------------------------------------------------------
# NIfTI + S6-shaped catalog stand-ins (no DB, no MinIO), for driving S7's real `_execute`
# ---------------------------------------------------------------------------------------------


def nifti_bytes(array: np.ndarray, affine_ras: np.ndarray) -> bytes:
    """A real NIfTI-1 .nii.gz whose RAW sform equals `affine_ras`. Geometry is handed to ITK in
    LPS (ITK converts to RAS itself on write -- the S6 lesson) and then checked in the header."""
    lps = LPS_TO_RAS @ affine_ras
    spacing = np.linalg.norm(lps[:3, :3], axis=0)
    direction = lps[:3, :3] / spacing
    image = sitk.GetImageFromArray(np.ascontiguousarray(array, dtype=np.float32))
    image.SetSpacing(tuple(float(s) for s in spacing))
    image.SetDirection(tuple(float(v) for v in direction.flatten()))
    image.SetOrigin(tuple(float(v) for v in lps[:3, 3]))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "v.nii.gz")
        sitk.WriteImage(image, path)
        on_disk = read_nifti_sform(path)
        assert np.allclose(on_disk, affine_ras, atol=1e-3), (on_disk, affine_ras)
        with open(path, "rb") as fh:
            return fh.read()


@cache
def phantom_nifti(name: str) -> bytes:
    ph = phantom(name)
    return nifti_bytes(ph.volume, ph.grid.affine_ras)


@dataclass
class S6Stub:
    series: Series
    s6: S6Inputs
    client: StubSilverClient
    volume_bytes: bytes
    meta_bytes: bytes
    meta: dict[str, Any]


def make_s6_stub(
    volume_bytes: bytes,
    array_shape: tuple[int, int, int],
    affine_ras: np.ndarray,
    *,
    modality: str = "CT",
    hu_valid: bool | None = None,
    pseudo_sop_uids: list[str] | None = None,
    series_id: UUID | None = None,
    meta_affine: np.ndarray | None = None,
) -> S6Stub:
    sid = series_id or uuid4()
    run_id = uuid4()
    meta = build_meta_json(
        pipeline_version="test",
        stage_version="1.0.0",
        series_id=sid,
        pseudo_series_uid="2.25.1",
        modality=modality,
        shape=array_shape,
        affine_ras_matrix=affine_ras if meta_affine is None else meta_affine,
        target_spacing_mm=1.0,
        original_row_spacing_mm=1.0,
        original_column_spacing_mm=1.0,
        slice_spacing_mm=1.0,
        dicom_slice_thickness_mm=1.0,
        dicom_spacing_between_slices_mm=None,
        params_version="1",
        intensity_units="hounsfield_units" if modality == "CT" else "arbitrary",
        hu_semantics_valid=(modality == "CT") if hu_valid is None else hu_valid,
        rescale_slope=1.0,
        rescale_intercept=0.0,
        padding_value=-1024.0,
        instance_count=len(pseudo_sop_uids or []),
        spacing_uniform=True,
        orientation_consistent=True,
        slice_order_pseudo_sop_uids=list(pseudo_sop_uids or []),
        produced_by_run=run_id,
    )
    meta_bytes = json.dumps(meta, sort_keys=True, indent=2).encode("utf-8")
    vkey, mkey = f"{sid}/volume.nii.gz", f"{sid}/meta.json"
    client = StubSilverClient({vkey: volume_bytes, mkey: meta_bytes})
    series = Series(
        id=sid,
        study_id=uuid4(),
        pseudo_series_uid="2.25.1",
        modality=modality,
        instance_count=len(pseudo_sop_uids or []),
        quality_status="pass",
    )
    run = TransformRun(
        id=run_id, stage="S6", subject_type="series", subject_id=sid, status="success"
    )

    def _art(kind: str, fmt: str, key: str, data: bytes) -> Artifact:
        return Artifact(
            id=uuid4(),
            series_id=sid,
            kind=kind,
            format=fmt,
            zone="silver",
            object_key=key,
            sha256=sha256_of_bytes(data),
            bytes=len(data),
            produced_by_run=run_id,
        )

    s6 = S6Inputs(
        run=run,
        volume=_art("volume_nifti", "nifti-1-gzip", vkey, volume_bytes),
        meta=_art("volume_meta", "json", mkey, meta_bytes),
    )
    return S6Stub(series, s6, client, volume_bytes, meta_bytes, meta)
