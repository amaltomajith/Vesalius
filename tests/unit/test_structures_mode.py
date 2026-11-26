"""Structures mode (refs/INTEGRATION.md §2-3): the pure pieces of S8-S11, no DB, no model.

The service chain with a real database is covered by tests/integration/test_structures_pipeline.py
and the real TotalSegmentator engine by a live run inside the api image.
"""

from __future__ import annotations

import json
from uuid import uuid4

import numpy as np
import pytest
import trimesh

from pipeline.stages.s8_segment import structures_params as sp
from pipeline.stages.s8_segment.structures import measure_structures
from pipeline.stages.s9_surface_extraction import structure_mesh_io as sm_io
from pipeline.stages.s9_surface_extraction import structure_meshing as sm
from pipeline.stages.s10_mesh_postprocess import structures_budget as sb
from pipeline.stages.s11_export import group_glb
from pipeline.stages.s11_export.structures_export import build_structures_manifest

AFFINE = np.array([[2.0, 0, 0, -40.0], [0, 2.0, 0, -40.0], [0, 0, 2.0, -40.0], [0, 0, 0, 1.0]])


def _labels() -> np.ndarray:
    lab = np.zeros((40, 40, 40), np.int32)
    lab[5:15, 5:15, 5:15] = 5  # a 10-voxel cube
    lab[20:35, 22:30, 10:30] = 10  # a box
    return lab


# ---- S8b group map ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label, group",
    [
        ("vertebrae_T7", "skeletal"),
        ("rib_left_4", "skeletal"),
        ("skull", "skeletal"),
        ("costal_cartilages", "skeletal"),  # added to the refs' table
        ("heart", "cardiovascular"),
        ("superior_vena_cava", "cardiovascular"),  # added
        ("liver", "visceral"),
        ("kidney_left", "visceral"),
        ("lung_upper_lobe_left", "respiratory"),
        ("gluteus_maximus_right", "muscular"),
        ("brain", "nervous"),
        ("thyroid_gland", "endocrine"),
        ("kidney_cyst_left", "other"),  # not `kidney_*`: the refs' visceral list names both kidneys
        ("prostate", "other"),
    ],
)
def test_the_group_map_follows_the_refs_and_never_drops_a_label(label: str, group: str) -> None:
    assert sp.group_for(label) == group


def test_every_refs_group_is_present_and_other_is_last() -> None:
    assert list(sp.GROUP_ORDER) == [
        "skeletal",
        "cardiovascular",
        "visceral",
        "respiratory",
        "muscular",
        "nervous",
        "endocrine",
        "other",
    ]


# ---- S8 measurements ----------------------------------------------------------------------------


def test_measurements_are_exact_and_in_world_space() -> None:
    lab = _labels()
    hu = np.where(lab == 5, 40.0, np.where(lab == 10, 900.0, -1000.0))
    by = {
        s["node"]: s for s in measure_structures(lab, hu, AFFINE, {5: "kidney_left", 10: "sternum"})
    }
    k = by["kidney_left"]
    assert k["volume_ml"] == 8.0  # 1000 voxels x 8 mm^3
    assert k["hu_mean"] == 40.0 and k["hu_std"] == 0.0
    assert k["bbox_mm"] == [[-30.0, -30.0, -30.0], [-12.0, -12.0, -12.0]]
    # (k, j, i) index order -> world (x=i, y=j, z=k): the box spans i 10..29, j 22..29, k 20..34
    assert by["sternum"]["bbox_mm"] == [[-20.0, 4.0, 0.0], [18.0, 18.0, 28.0]]
    assert by["sternum"]["group"] == "skeletal" and by["kidney_left"]["group"] == "visceral"


def test_a_label_the_engine_did_not_name_is_kept_not_dropped() -> None:
    lab = _labels()
    out = measure_structures(lab, np.zeros_like(lab, dtype=float), AFFINE, {5: "liver"})
    assert {s["node"] for s in out} == {"liver", "label_10"}
    assert next(s for s in out if s["node"] == "label_10")["group"] == "other"


# ---- S9 per-structure meshing -------------------------------------------------------------------


def test_each_structure_is_meshed_in_world_space() -> None:
    meshes, not_meshed = sm.mesh_all(
        _labels(), AFFINE, [{"node": "a", "label_id": 5}, {"node": "b", "label_id": 10}]
    )
    assert not_meshed == []
    a = next(m for m in meshes if m.node == "a")
    # the cube's voxel centres run -30..-12 mm; the blurred 0.5 isosurface sits about a voxel out
    assert np.all(a.vertices_ras.min(axis=0) > -33) and np.all(a.vertices_ras.max(axis=0) < -9)
    assert trimesh.Trimesh(a.vertices_ras, a.faces).is_watertight


def test_a_structure_touching_the_volume_edge_still_closes() -> None:
    lab = np.zeros((20, 20, 20), np.int32)
    lab[0:8, 0:8, 0:8] = 3
    v, f = sm.mesh_structure(lab, 3, AFFINE)
    assert trimesh.Trimesh(v, f).is_watertight


def test_a_structure_too_small_to_mesh_is_reported_not_dropped() -> None:
    lab = np.zeros((10, 10, 10), np.int32)
    lab[5, 5, 5] = 7
    meshes, not_meshed = sm.mesh_all(lab, AFFINE, [{"node": "speck", "label_id": 7}])
    assert meshes == [] and not_meshed[0]["node"] == "speck"


def test_the_mesh_pack_is_byte_deterministic_and_round_trips() -> None:
    meshes, _ = sm.mesh_all(
        _labels(), AFFINE, [{"node": "b", "label_id": 10}, {"node": "a", "label_id": 5}]
    )
    normals = {m.node: np.ones_like(m.vertices_ras) for m in meshes}
    one, two = sm_io.pack(meshes, normals), sm_io.pack(list(reversed(meshes)), normals)
    assert one == two
    back = sm_io.unpack(one)
    assert [m.node for m in back] == ["a", "b"]
    assert np.array_equal(back[0].faces, next(m for m in meshes if m.node == "a").faces)
    assert set(sm_io.unpack_normals(one)) == {"a", "b"}


# ---- S10 budgets --------------------------------------------------------------------------------


def test_budgets_are_proportional_floored_and_never_inflate() -> None:
    raw = {"femur_left": 300_000, "rib_left_1": 30_000, "hip_left": 1_000, "tiny_bone": 500}
    targets = sb.allocate(raw, {n: "skeletal" for n in raw})
    assert targets["femur_left"] > targets["rib_left_1"] > targets["hip_left"]
    # 120k x 1000 / 331.5k = 362 faces, below the floor, so it is lifted to exactly the floor
    assert targets["hip_left"] == sb.FLOOR_FACES
    assert targets["tiny_bone"] == 500  # the floor never inflates a structure past its own count
    unfloored = targets["femur_left"] + targets["rib_left_1"]
    assert unfloored <= sb.FACE_BUDGET_V1["skeletal"]


def test_a_multi_part_structure_keeps_every_part() -> None:
    """The verified single-mesh post-processor keeps only the largest component; anatomy like
    costal cartilages is legitimately multi-part, so S10 structures mode must keep all parts."""
    a = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    b = trimesh.creation.icosphere(subdivisions=3, radius=6.0)
    b.apply_translation([40.0, 0, 0])
    both = trimesh.util.concatenate([a, b])
    v, f, n, stats = sb.process_structure(np.asarray(both.vertices), np.asarray(both.faces), 800)
    assert stats["components_kept"] == 2
    assert v[:, 0].max() > 40.0  # the smaller, farther part survived
    assert len(n) == len(v) and len(f) <= 800 + 8


# ---- S11 group GLBs and the manifest ------------------------------------------------------------


def _glb(names: list[str]) -> bytes:
    parts = []
    for name in names:
        m = trimesh.creation.box()
        m.apply_translation([len(name), 0, 0])  # placed by name, not by list position
        parts.append(
            (name, np.asarray(m.vertices), np.asarray(m.faces), np.asarray(m.vertex_normals))
        )
    return group_glb.build_group_glb(parts)


def test_node_names_survive_export_exactly() -> None:
    names = ["vertebrae_T7", "rib_left_4", "kidney_left", "lung_upper_lobe_left"]
    glb = _glb(names)
    assert group_glb.glb_node_names(glb) == sorted(names)
    # the reader is independent of the writer: it parses the GLB's JSON chunk itself
    assert group_glb.glb_json(glb)["asset"]["version"] == "2.0"  # type: ignore[index]
    assert _glb(list(reversed(names))) == glb  # byte-deterministic regardless of input order


def test_the_node_name_reader_can_fail() -> None:
    with pytest.raises(ValueError):
        group_glb.glb_json(b"not a glb at all, just bytes")
    assert group_glb.glb_node_names(_glb(["heart"])) != ["Heart"]


def test_groups_share_one_origin() -> None:
    v1 = np.array([[100.0, 0, 0], [110.0, 0, 0]])
    v2 = np.array([[-50.0, 20, 0], [-40.0, 30, 0]])
    off = group_glb.shared_offset_mm([v1, v2])
    a, _ = group_glb.to_gltf(v1, np.zeros_like(v1), off)
    b, _ = group_glb.to_gltf(v2, np.zeros_like(v2), off)
    # the same world point maps to the same glTF point whichever group it is in
    p, _ = group_glb.to_gltf(np.array([[100.0, 0, 0]]), np.zeros((1, 3)), off)
    assert np.allclose(a[0], p[0])
    assert np.allclose(np.vstack([a, b]).min(axis=0) + np.vstack([a, b]).max(axis=0), 0, atol=1e-12)


def test_the_manifest_carries_every_field_the_contract_requires() -> None:
    doc = json.loads(
        build_structures_manifest(
            series_id=uuid4(),
            pipeline_version="0.1.0",
            segmentation={
                "engine": {
                    "engine": "totalsegmentator",
                    "engine_version": "2.18.0",
                    "weights_sha256": "ab",
                },
                "label_set_version": "totalseg_total_v2",
                "group_map_version": "v1",
            },
            offset_mm=np.array([1.0, 2.0, 3.0]),
            deface={"required": True, "applied": True, "method": "geometric_v1"},
            groups=[{"id": "skeletal", "structures": []}],
            not_meshed=[],
            study={"modality": "CT", "body_part": "CHEST"},
        )
    )
    assert doc["contract_version"] == "1.0"
    assert (
        doc["geometry"]["export_frame"] == "glTF_Y_up_m"
        and doc["geometry"]["shared_origin"] is True
    )
    deface = doc["deface"]
    assert {k: deface[k] for k in ("required", "applied", "method")} == {
        "required": True,
        "applied": True,
        "method": "geometric_v1",
    }
    assert "structures_cut" in deface  # head studies record the structures face cut
    assert doc["segmentation"]["model"] == "totalsegmentator"
    assert {"groups", "study", "series_id", "pipeline_version"} <= set(doc)


# ---- head studies: every structure cut to S7's face region --------------------------------------


def test_the_face_cut_clears_exactly_the_region_and_counts_what_it_removed() -> None:
    from pipeline.stages.s8_segment.structures import cut_face_region

    lab = np.zeros((20, 20, 20), np.int32)
    lab[:, :, :] = 91  # "skull" everywhere
    affine = np.eye(4)  # index (i, j, k) == RAS (R, A, S) in mm
    cut, removed = cut_face_region(lab, affine, a_min=15.0, s_min=10.0)
    # A is the j axis (array axis 1), S the k axis (array axis 0)
    assert not cut[10:, 15:, :].any()  # inside: A >= 15 and S >= 10
    assert cut[:10, 15:, :].all() and cut[10:, :15, :].all()  # outside either threshold: kept
    assert removed == {91: 10 * 5 * 20}
    assert lab.all()  # the input is never mutated


# ---- S9: no 3 mm staircase (structures_params_version 2) -----------------------------------------


def _mean_dihedral_deg(v: np.ndarray, f: np.ndarray) -> float:
    return float(np.degrees(trimesh.Trimesh(v, f, process=True).face_adjacency_angles).mean())


def test_the_blur_is_sized_in_mm_per_axis_and_never_below_one_voxel() -> None:
    aff = np.diag([0.5, 0.75, 3.0, 1.0])  # i, j, k voxel sizes
    k, j, i = sm.blur_sigma_voxels(aff)
    assert (k, j, i) == (1.0, pytest.approx(sm.BLUR_SIGMA_MM / 0.75), sm.BLUR_SIGMA_MM / 0.5)
    assert sm.structures_algorithm_params()["blur_sigma_mm"] == sm.BLUR_SIGMA_MM


def test_a_mask_built_from_3mm_blocks_meshes_without_the_staircase() -> None:
    # what TotalSegmentator --fast hands back: a sphere segmented on a 3 mm grid, returned on 1 mm
    centres = np.indices((16, 16, 16)).astype(float) * 3 + 1.5
    coarse = (np.linalg.norm(centres - 24, axis=0) < 12).astype(np.int32)
    lab = np.kron(coarse, np.ones((3, 3, 3), np.int32)) * 7
    v, f = sm.mesh_structure(lab, 7, np.eye(4))
    smooth = _mean_dihedral_deg(v, f)
    # control: the v1 blur (one 1 mm voxel) on the same mask keeps the steps
    from scipy import ndimage
    from skimage import measure

    field = ndimage.gaussian_filter(np.pad((lab == 7).astype(np.float32), 4), 1.0)
    v1, f1, _n, _v = measure.marching_cubes(field, 0.5)
    stepped = _mean_dihedral_deg(v1, f1)
    assert stepped > 7.0, stepped  # the check can fail
    assert smooth < 6.0, smooth
