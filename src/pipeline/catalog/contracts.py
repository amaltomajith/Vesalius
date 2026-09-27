"""Published data contracts (DATA-MODEL.md "Data contract"). The dict below is the single code-side
source for `volume_contract` v1.0; `publish_contract` writes it to `public.data_contracts`.

Published versions are IMMUTABLE: re-publishing identical content is a no-op, re-publishing the
same (name, version) with different content raises -- a consumer that pinned "1.0" must never see
it change under them (breaking changes bump the major version instead).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pipeline.catalog.models import DataContract
from pipeline.common.errors import PipelineError

VOLUME_CONTRACT_V1_0: dict[str, Any] = {
    "name": "volume_contract",
    "version": "1.0",
    "artifact_kind": "volume_nifti",
    "format": "nifti-1-gzip",
    "array": {"dimensions": 3, "axis_order": ["z", "y", "x"], "dtype": "float32"},
    "spatial": {
        "coordinate_frame": "RAS",
        "spacing_units": "mm",
        "isotropic": True,
        "affine": "stored in NIfTI header, srow_x/y/z",
    },
    "intensity": {
        "units": "hounsfield_units",
        "applies_to_modality": ["CT"],
        "rescale_applied": True,
        "air_value": -1000.0,
        "water_value": 0.0,
    },
    "nullability": {
        "padding_value": -1024.0,
        "note": "Regions outside the reconstruction circle are padded, not missing",
    },
    "guarantees": [
        "de-identified per PS3.15 basic profile",
        "defaced when body_part requires it",
        "slice order validated by position projection",
        "spacing uniformity verified",
    ],
}

MESH_CONTRACT_V1_0: dict[str, Any] = {
    "name": "mesh_contract",
    "version": "1.0",
    "artifact_kind": "structures_manifest",
    "format": "json",
    "layout": {
        "manifest": "gold/{series_id}/mesh/structures.json",
        "groups": "gold/{series_id}/mesh/{group}.glb -- one GLB per body-system group",
    },
    "geometry": {
        "export_frame": "glTF_Y_up_m",
        "units": "metres",
        "shared_origin": True,
        "note": "every group of a study is written with one centring offset, so layers align",
    },
    "nodes": {
        "naming": "exact segmentation label, lowercase, underscore-separated, unmodified",
        "one_node_per_structure": True,
    },
    "per_structure": ["node", "label", "faces", "volume_ml", "hu_mean", "hu_std", "bbox_mm"],
    "bbox_frame": "RAS_mm",
    "deface": {
        "fields": ["required", "applied", "method"],
        "consumer_rule": "refuse the study when required and not applied",
    },
    "guarantees": [
        "produced from the S7-gated (defaced when required) volume only",
        "served from gold only",
        "unmapped labels are listed under the group 'other', never dropped",
    ],
}

PUBLISHED_CONTRACTS: tuple[dict[str, Any], ...] = (VOLUME_CONTRACT_V1_0, MESH_CONTRACT_V1_0)


class ContractConflictError(PipelineError):
    """A published (name, version) already exists with different content."""


def _canonical(doc: dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def publish_contract(session: Session, doc: dict[str, Any]) -> bool:
    """Returns True when a row was inserted, False when an identical one already existed."""
    existing = session.execute(
        select(DataContract).where(
            DataContract.name == doc["name"], DataContract.version == doc["version"]
        )
    ).scalar_one_or_none()
    if existing is not None:
        if _canonical(existing.schema) != _canonical(doc):
            raise ContractConflictError(
                f"{doc['name']} v{doc['version']} is already published with different content"
            )
        return False
    session.add(DataContract(name=doc["name"], version=doc["version"], schema=doc))
    session.flush()
    return True


def publish_all(session: Session) -> int:
    return sum(1 for doc in PUBLISHED_CONTRACTS if publish_contract(session, doc))
