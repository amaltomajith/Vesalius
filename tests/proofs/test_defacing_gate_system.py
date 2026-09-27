"""TESTING.md Proof 2, at the SYSTEM level -- the pipeline cannot emit an identifying face mesh.

`test_defacing_gate.py` proves the gate's pure decision function. That is necessary and not
sufficient: the claim is about what the PIPELINE can produce. Here real stages run on the real
stack and we look at what exists afterwards.

  A. An undefaced head cannot reach a mesh: the gate raises, every downstream stage (including
     `extract_surface(..., skip_deface=True)`) SKIPS with the gate's code, and no mask, mesh or
     gold artifact exists for the series.
  B. The attack the gate exists to stop: the defaced volume's object in silver is replaced by the
     RAW volume. The gate re-hashes what it reads, refuses, and nothing downstream runs.
  C. The evidence: the same head study meshed WITH and WITHOUT defacing. The skin mesh reaches the
     nose only without defacing; the skull base and the sinus/maxilla bone are unchanged; a
     side-by-side sagittal picture is written to tests/proofs/_out/.
  D. A real chest chain passes the gate and RECORDS `required=False, reason=body_part=CHEST`
     ("absence of defacing must always be explained").

Same caveat as every phantom test in this project: the synthetic head shows the mechanism works
and is orientation-independent, not that defacing is effective on real anatomy.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from pipeline.catalog.models import Artifact, TransformRun
from pipeline.common.config import get_settings
from pipeline.common.errors import DefacingGateError
from pipeline.stages.s7_deface.gate import open_defacing_gate
from pipeline.stages.s7_deface.service import deface_series
from pipeline.stages.s8_segment import params as s8_params
from pipeline.stages.s8_segment.segmentation import segment
from pipeline.stages.s8_segment.service import segment_series
from pipeline.stages.s9_surface_extraction import mesh_io
from pipeline.stages.s9_surface_extraction import params as s9_params
from pipeline.stages.s9_surface_extraction.mesh_extract import extract_mesh
from pipeline.stages.s9_surface_extraction.service import extract_surface
from pipeline.stages.s10_mesh_postprocess.service import postprocess_mesh_series
from pipeline.stages.s11_export.service import export_series
from pipeline.storage.object_store import get_bytes, put_bytes
from tests.fixtures import head_phantom as hp
from tests.fixtures.deface_runs import AIR_VALUE, phantom, phantom_nifti
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import _chest_through_s10, _seed_head

BASE = "lps_native_default"
NOSE_TIP = np.array([0.0, 123.0, -10.0])  # head_phantom.NOSE centre, front of the face
OUT_DIR = Path(__file__).parent / "_out"

DOWNSTREAM_KINDS = ("mask", "surface_mesh_raw", "surface_mesh_optimized")


def _artifacts(session, sid: UUID, *, kinds=None, zone=None) -> list[Artifact]:  # type: ignore[no-untyped-def]
    stmt = select(Artifact).where(Artifact.series_id == sid)
    if kinds:
        stmt = stmt.where(Artifact.kind.in_(kinds))
    if zone:
        stmt = stmt.where(Artifact.zone == zone)
    return list(session.execute(stmt).scalars())


def _assert_nothing_was_produced(session, sid: UUID) -> None:  # type: ignore[no-untyped-def]
    assert _artifacts(session, sid, kinds=DOWNSTREAM_KINDS) == []
    assert _artifacts(session, sid, zone="gold") == []


# ---- A. an undefaced head cannot reach a mesh ------------------------------------------------


@integration
def test_an_undefaced_head_cannot_reach_a_mesh_even_with_skip_deface_true(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)  # S6 only: no S7 run

    with pytest.raises(DefacingGateError) as exc:
        open_defacing_gate(pipeline_session, silver_minio_client, settings, sid)
    assert exc.value.reason_code == "GATE_NO_S7_RUN"

    seg = segment_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    surf = extract_surface(
        pipeline_session, silver_minio_client, settings, sid, "bone", skip_deface=True
    )
    opt = postprocess_mesh_series(pipeline_session, silver_minio_client, settings, sid, "bone")
    exp = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)

    assert seg.skipped and surf.skipped and exp.skipped
    assert surf.skip_reason == "defacing_gate_refused:GATE_NO_S7_RUN"
    assert exp.skip_reason == "defacing_gate_refused:GATE_NO_S7_RUN"
    assert opt.skipped and opt.skip_reason == "series_has_no_s9_mesh"
    _assert_nothing_was_produced(pipeline_session, sid)


# ---- B. swapping the raw volume in for the defaced one ---------------------------------------


@integration
def test_replacing_the_defaced_volume_with_the_raw_one_is_refused(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    assert not deface_series(pipeline_session, silver_minio_client, settings, sid).quarantined
    (defaced,) = _artifacts(pipeline_session, sid, kinds=("volume_defaced",))

    # the attack: put the RAW (un-defaced) bytes where the defaced volume is expected
    raw = phantom_nifti(BASE)
    assert raw != get_bytes(silver_minio_client, settings.minio_silver_bucket, defaced.object_key)
    put_bytes(
        silver_minio_client,
        settings.minio_silver_bucket,
        defaced.object_key,
        raw,
        "application/gzip",
    )

    with pytest.raises(DefacingGateError) as exc:
        open_defacing_gate(pipeline_session, silver_minio_client, settings, sid)
    assert exc.value.reason_code == "GATE_DEFACED_HASH_MISMATCH"

    surf = extract_surface(pipeline_session, silver_minio_client, settings, sid, "bone")
    exp = export_series(pipeline_session, silver_minio_client, gold_minio_client, settings, sid)
    assert surf.skipped and surf.gate_reason_code == "GATE_DEFACED_HASH_MISMATCH"
    assert exp.skipped
    _assert_nothing_was_produced(pipeline_session, sid)


# ---- C. the same head, meshed with and without defacing --------------------------------------


def _mesh_from_volume(volume: np.ndarray, affine: np.ndarray, preset: str) -> np.ndarray:
    """RAS-mm vertices via the SAME S8/S9 algorithms, but straight from an array -- used only for
    the un-defaced baseline that the pipeline itself refuses to produce."""
    lo, hi = s8_params.preset_bounds(preset)
    mask = segment(
        volume,
        lo=lo,
        hi=hi,
        closing_iterations=s8_params.CLOSING_ITERATIONS,
        opening_iterations=s8_params.OPENING_ITERATIONS,
    ).mask
    result = extract_mesh(
        volume,
        mask,
        affine,
        level=s9_params.level_for(preset),
        air_value=AIR_VALUE,
        dilation_voxels=s9_params.DILATION_VOXELS,
        step_size=s9_params.STEP_SIZE,
        world_bbox_tolerance_mm=s9_params.WORLD_BBOX_TOLERANCE_MM,
    )
    return np.asarray(result.vertices_ras)


def _pipeline_mesh(session, silver, settings, sid: UUID, preset: str) -> np.ndarray:  # type: ignore[no-untyped-def]
    segment_series(session, silver, settings, sid, preset)
    assert not extract_surface(session, silver, settings, sid, preset).skipped
    arts = _artifacts(session, sid, kinds=("surface_mesh_raw",))
    art = next(a for a in arts if a.produced_by_run == _s9_run_for(session, sid, preset))
    verts, _ = mesh_io.load_mesh(get_bytes(silver, settings.minio_silver_bucket, art.object_key))
    return np.asarray(verts)


def _s9_run_for(session, sid: UUID, preset: str) -> UUID:  # type: ignore[no-untyped-def]
    runs = session.execute(
        select(TransformRun)
        .where(
            TransformRun.stage == "S9",
            TransformRun.subject_id == sid,
            TransformRun.status == "success",
        )
        .order_by(TransformRun.started_at.desc())
    ).scalars()
    return next(r.id for r in runs if r.parameters["algorithm"]["preset"] == preset)


def _in_box(v: np.ndarray, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> int:
    return int(np.all((v >= np.array(lo)) & (v <= np.array(hi)), axis=1).sum())


def _panel(skin: np.ndarray, bone: np.ndarray, title: str) -> Image.Image:
    """Sagittal projection (anterior to the right, superior up), 1 px per mm."""
    w, h = 300, 260
    img = Image.new("RGB", (w, h + 22), "white")
    d = ImageDraw.Draw(img)

    def xy(v: np.ndarray) -> np.ndarray:
        return np.stack([v[:, 1] + 140, h - (v[:, 2] + 125)], axis=1)  # (y, z) -> pixel

    for verts, colour in ((skin, (170, 170, 170)), (bone, (60, 60, 60))):
        for x, y in xy(verts)[::3]:
            if 0 <= x < w and 0 <= y < h:
                d.point((x, y + 22), fill=colour)
    nx, ny = xy(NOSE_TIP[None, :])[0]
    d.ellipse((nx - 18, ny + 22 - 18, nx + 18, ny + 22 + 18), outline=(200, 0, 0))
    d.text((6, 4), title, fill=(0, 0, 0))
    return img


@integration
def test_defacing_removes_the_face_but_leaves_the_anatomy_and_a_picture_shows_it(
    pipeline_session, silver_minio_client
) -> None:
    settings = get_settings()
    ph = phantom(BASE)
    sid = _seed_head(pipeline_session, silver_minio_client, settings)
    assert not deface_series(pipeline_session, silver_minio_client, settings, sid).quarantined

    skin_with = _mesh_from_volume(ph.volume, ph.grid.affine_ras, "skin_surface")
    bone_with = _mesh_from_volume(ph.volume, ph.grid.affine_ras, "bone")
    skin_defaced = _pipeline_mesh(
        pipeline_session, silver_minio_client, settings, sid, "skin_surface"
    )
    bone_defaced = _pipeline_mesh(pipeline_session, silver_minio_client, settings, sid, "bone")

    # the face: present without defacing, absent with it (the thesis, at mesh level)
    d_with = np.linalg.norm(skin_with - NOSE_TIP, axis=1).min()
    d_defaced = np.linalg.norm(skin_defaced - NOSE_TIP, axis=1).min()
    assert d_with < 20.0, "the un-defaced skin mesh should reach the nose"
    assert d_defaced > 25.0, "the defaced skin mesh must not"
    assert (skin_with[:, 1] > 110.0).sum() > 0 and (skin_defaced[:, 1] > 110.0).sum() == 0

    # the anatomy: skull base and the maxilla/sinus bone are unchanged by defacing
    boxes = {
        "skull_base": ((-30.0, -60.0, -96.0), (30.0, -10.0, -80.0)),
        "maxilla_sinus": ((-35.0, 35.0, -70.0), (35.0, 95.0, -5.0)),
    }
    for name, (lo, hi) in boxes.items():
        before, after = _in_box(bone_with, lo, hi), _in_box(bone_defaced, lo, hi)
        assert before > 1000, f"{name}: the region should hold real bone geometry"
        assert abs(after - before) / before < 0.03, (name, before, after)

    OUT_DIR.mkdir(exist_ok=True)
    left = _panel(skin_with, bone_with, "WITHOUT defacing (refused by the gate)")
    right = _panel(skin_defaced, bone_defaced, "WITH defacing (what the pipeline emits)")
    sheet = Image.new("RGB", (left.width + right.width + 10, left.height), "white")
    sheet.paste(left, (0, 0))
    sheet.paste(right, (left.width + 10, 0))
    path = OUT_DIR / "defacing_side_by_side.png"
    sheet.save(path)
    assert Image.open(io.BytesIO(path.read_bytes())).size == sheet.size


# ---- D. a real chest chain records why defacing was not required -----------------------------


@integration
def test_a_real_chest_passes_the_gate_and_records_why_defacing_was_not_required(
    pipeline_session,
    bronze_minio_client,
    silver_minio_client,
    no_ocr_engine,
    tmp_path: Path,
) -> None:
    settings = get_settings()
    sid, _ = _chest_through_s10(
        pipeline_session, bronze_minio_client, silver_minio_client, settings, tmp_path, "p2.zip"
    )
    run_id = _s9_run_for(pipeline_session, sid, "soft_tissue")
    run = pipeline_session.get(TransformRun, run_id)
    assert run is not None and run.status == "success"
    deface = run.parameters["deface"]
    assert deface["required"] is False and deface["applied"] is False
    assert deface["reason"] == "body_part=CHEST"
    assert _artifacts(pipeline_session, sid, kinds=("surface_mesh_raw",))


def test_the_head_phantoms_geometry_is_what_the_assertions_assume() -> None:
    """Guards the constants above against a fixture change that would make C vacuous."""
    assert tuple(hp.NOSE[0]) == (0.0, 105.0, -10.0) and hp.NOSE[1][1] == 18.0  # tip at y=123
    assert hp.SKULL_BASE[0] == (0.0, -35.0, -88.0) and hp.SINUS[0] == (0.0, 66.0, -30.0)
