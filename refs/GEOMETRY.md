# Geometry Reference

Segmentation, defacing, surface extraction, mesh optimisation, GLB export.

---

## Hounsfield Unit reference

CT only. MR has no standardised scale.

| Tissue | HU range |
|---|---|
| Air | −1000 |
| Lung parenchyma | −900 to −500 |
| Fat | −120 to −50 |
| Water | 0 |
| Soft tissue / muscle | +20 to +80 |
| Contrast-enhanced blood | +100 to +300 |
| Cancellous (trabecular) bone | +300 to +400 |
| Cortical bone | +500 to +1900 |
| Metal / implant | > +2000 |

### Threshold presets

Versioned parameters — record in `transform_runs`, never hardcode inline.

```python
PRESETS_V1 = {
    "bone":         {"min":  300, "max":  3000},
    "bone_dense":   {"min":  500, "max":  3000},
    "soft_tissue":  {"min":  -50, "max":   300},
    "lung":         {"min": -900, "max":  -500},
    "vessel_ce":    {"min":  150, "max":   500},   # requires contrast
    "skin_surface": {"min": -200, "max":  3000},   # outer body surface
}
```

`skin_surface` is the preset that produces a face. It must never run on head anatomy without
defacing.

### Morphological cleanup

Raw thresholds produce speckle. Order matters:

```python
from scipy import ndimage

mask = (volume >= lo) & (volume <= hi)
mask = ndimage.binary_closing(mask, iterations=2)    # seal small gaps
mask = ndimage.binary_opening(mask, iterations=1)    # drop speckle
labels, n = ndimage.label(mask)
if n > 1:
    sizes = ndimage.sum(mask, labels, range(1, n + 1))
    mask = labels == (np.argmax(sizes) + 1)          # largest component
```

**Sanity gates:** empty mask means the threshold is wrong for this study. A mask covering
>90% of the volume means the same. Both fail the stage rather than producing a useless mesh.

---

## Defacing

### When it is required

Mandatory when `BodyPartExamined` (or the inferred body part) is any of:

```
HEAD, SKULL, BRAIN, FACE, SINUS, NECK, TMJ, ORBIT, MAXILLA, MANDIBLE,
CSPINE, C-SPINE, HEADNECK, PTS_HEAD
```

**Unresolvable body part means quarantine, not skip.** An unknown study could be a head.

### Why

A surface mesh of a head CT reconstructs a recognisable face. This re-identification channel
is *created by the pipeline* — the source DICOM is only latently identifying, the mesh is
overtly so. Tag scrubbing does nothing about it.

### Approaches

**Option A — external tool.** `pydeface` operates on NIfTI, is well established, but depends
on FSL. Heavier install; more defensible in a write-up.

**Option B — geometric, pure Python.** Adequate for this project and easier to explain:

1. Locate the head bounding box from the soft-tissue mask
2. Identify the anterior region — the front ~35% along the A-P axis, above the level of the
   mandible
3. Within that region, find the outer air-tissue boundary
4. Displace or zero voxels within a few mm of that boundary

Preserve interior structures — the aim is to destroy the *surface*, not the anatomy.

```python
def deface_region(volume, affine, anterior_fraction=0.35, superior_fraction=0.55):
    """Zero the outer facial surface. Returns (defaced_volume, region_mask)."""
    ...
```

### Verification

Record a `transform_run` for every study, including those where defacing was not required —
with the reason. Absence of defacing must always be explained, never merely absent.

**Demonstration for the report:** render the mesh with and without defacing on the same head
study, side by side. This is the single most persuasive artifact in the project.

---

## Surface extraction

```python
from skimage import measure

verts, faces, normals, values = measure.marching_cubes(
    volume,                      # the intensity volume, not the mask
    level=threshold,             # threshold in HU
    spacing=(sz, sy, sx),        # MUST match array axis order
    step_size=1,                 # >1 downsamples: faster, coarser
    allow_degenerate=False,
)
```

**Two things to get right:**

1. `spacing` must be in the same axis order as the array. If the array is `(z, y, x)`, spacing
   is `(sz, sy, sx)`. Getting this wrong produces a mesh that is stretched along one axis and
   looks *almost* correct.
2. Pass the **intensity volume with a level**, not a binary mask. Marching cubes on a binary
   mask produces blocky stair-stepped output; on intensity data with a threshold level it
   interpolates and produces smooth surfaces.

`step_size=2` reduces face count roughly fourfold and is a reasonable default for large
volumes. Record it as a parameter.

---

## Mesh post-processing

Order matters. Doing these out of order wastes work or degrades quality.

### 1. Largest connected component

```python
import trimesh

mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
components = mesh.split(only_watertight=False)
if len(components) > 1:
    mesh = max(components, key=lambda m: len(m.faces))
```

Do this **first** — no point smoothing scanner-table fragments you are about to discard.

### 2. Clean degenerate geometry

```python
mesh.update_faces(mesh.nondegenerate_faces())
mesh.update_faces(mesh.unique_faces())
mesh.remove_unreferenced_vertices()
```

### 3. Smoothing — Taubin, not Laplacian

Laplacian smoothing shrinks the mesh progressively; over 20 iterations a bone surface visibly
loses volume. Taubin alternates a positive and a negative step and preserves volume.

```python
trimesh.smoothing.filter_taubin(mesh, lamb=0.5, nu=-0.53, iterations=12)
```

`nu` must be slightly more negative than `−lamb` for volume preservation.

### 4. Decimation

```python
import pymeshlab

ms = pymeshlab.MeshSet()
ms.add_mesh(pymeshlab.Mesh(mesh.vertices, mesh.faces))
ms.meshing_decimation_quadric_edge_collapse(
    targetfacenum=200_000,
    preserveboundary=True,
    preservenormal=True,
    planarquadric=True,
)
```

Quadric edge collapse preserves shape far better than vertex clustering.

> **Project deviation (deployment to ARM64):** S10 uses `fast_simplification.simplify(points,
> faces, target_count=...)`, the same quadric edge-collapse family, because pymeshlab ships no
> aarch64 wheel. It has no `preserveboundary`/`preservenormal` options; this is recorded as an S10
> residual risk. Measured parity with the snippet above: exact target face count, byte-identical
> repeated runs, volume within 0.3% of pymeshlab's.

**Face budgets:** ~200k for a detailed model, ~50k for web-interactive, ~10k for a thumbnail.
Marching cubes on a full CT commonly yields 2–5 million faces, so decimation is not optional.

**Record input and output face counts.** The size/quality tradeoff is a question you will be
asked.

### 5. Normals

```python
mesh.fix_normals()
```

Do this last — decimation invalidates normals.

---

## GLB export

### Axis convention — the gotcha

DICOM is **LPS, millimetres**. glTF is **Y-up, right-handed, metres**. Exporting without
conversion produces a model that renders lying on its side and 1000× too large.

```python
import numpy as np

# LPS (x=Left, y=Posterior, z=Superior) -> glTF (x=Right, y=Up, z=Forward)
LPS_TO_GLTF = np.array([
    [-1, 0,  0, 0],   # Left      -> -Right
    [ 0, 0,  1, 0],   # Superior  ->  Up
    [ 0, 1,  0, 0],   # Posterior ->  Forward
    [ 0, 0,  0, 1],
], dtype=float)

MM_TO_M = np.diag([0.001, 0.001, 0.001, 1.0])

mesh.apply_transform(MM_TO_M @ LPS_TO_GLTF)
```

### Centring

Patient coordinates place the model far from the origin. Viewers frame poorly. Centre it and
**record the applied translation in the manifest** so the transform is reversible:

```python
offset = -mesh.bounding_box.centroid
mesh.apply_translation(offset)
```

### Export

```python
mesh.export("surface.glb")          # trimesh infers GLB from the extension
```

Optional Draco compression cuts file size substantially. Add only if payload size matters —
it introduces a decode dependency in the viewer.

### Validate what you wrote

```python
loaded = trimesh.load("surface.glb")
assert len(loaded.faces) == len(mesh.faces)
assert loaded.is_watertight == mesh.is_watertight
```

---

## Manifest

Every exported artifact set carries a manifest. This is what makes the rebuild test possible.

```json
{
  "contract_version": "1.0",
  "pipeline_version": "0.3.1",
  "series_id": "...",
  "artifacts": {
    "surface.glb":    {"sha256": "...", "bytes": 4194304, "faces": 198412},
    "volume.nii.gz":  {"sha256": "...", "bytes": 52428800},
    "thumbnail.png":  {"sha256": "...", "bytes": 48291}
  },
  "geometry": {
    "source_frame": "LPS_mm",
    "export_frame": "glTF_Y_up_m",
    "centring_offset_mm": [-12.4, 88.1, -204.7],
    "voxel_spacing_mm": [0.7, 0.7, 0.7]
  },
  "parameters": {
    "preset": "bone", "preset_version": "v1",
    "threshold_hu": 300, "step_size": 2,
    "smoothing": {"method": "taubin", "lamb": 0.5, "nu": -0.53, "iterations": 12},
    "target_faces": 200000
  },
  "deface": {"required": true, "applied": true, "method": "geometric_v1"}
}
```
