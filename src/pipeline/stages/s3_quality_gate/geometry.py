"""Per-instance re-read for S3. S2's stop_before_pixels=True reads aren't enough here --
cross-instance checks need per-instance geometry tags S2 only cached from the first
instance, and QG_PIXEL_DECODE_FAILED needs pixel data actually decoded, which a
stop_before_pixels dataset structurally cannot provide (.pixel_array raises immediately
regardless of real corruption, since PixelData was never parsed at all).

One full read per instance produces everything every check needs: extract_tags() is reused
unchanged (tag extraction doesn't care whether pixel data was also read), so no second,
separate lightweight read is done.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pydicom
from minio import Minio

from pipeline.catalog.models import Instance
from pipeline.common.config import Settings
from pipeline.common.dicom_geometry import MissingGeometryError, slice_position_from_values
from pipeline.common.errors import S3UnreadableInstanceError
from pipeline.stages.s2_parse_index.tags import extract_tags
from pipeline.storage.bronze import bronze_key


@dataclass(frozen=True)
class InstanceGeometry:
    instance_id: UUID
    content_hash: str
    tags: dict[str, Any]
    slice_position: float | None
    pixel_decode_error: str | None


def _read_bronze_bytes(minio_client: Minio, settings: Settings, content_hash: str) -> bytes:
    key = bronze_key(content_hash)
    response = minio_client.get_object(settings.minio_bronze_bucket, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def gather_instance_geometry(
    minio_client: Minio, settings: Settings, instances: list[Instance]
) -> list[InstanceGeometry]:
    out: list[InstanceGeometry] = []
    for inst in instances:
        try:
            data = _read_bronze_bytes(minio_client, settings, inst.content_hash)
            ds = pydicom.dcmread(io.BytesIO(data))  # full read -- S3 needs pixel_array too
        except Exception as exc:  # noqa: BLE001 - infra failure, not a verdict
            raise S3UnreadableInstanceError(
                {
                    "instance_id": str(inst.id),
                    "content_hash": inst.content_hash,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ) from exc

        tags = extract_tags(ds)

        try:
            position: float | None = slice_position_from_values(
                tags["ImageOrientationPatient"], tags["ImagePositionPatient"]
            )
        except MissingGeometryError:
            position = None

        pixel_decode_error: str | None = None
        try:
            _ = ds.pixel_array
        except Exception as exc:  # noqa: BLE001 - a check result, not an execution failure
            pixel_decode_error = f"{type(exc).__name__}: {exc}"

        out.append(
            InstanceGeometry(
                instance_id=inst.id,
                content_hash=inst.content_hash,
                tags=tags,
                slice_position=position,
                pixel_decode_error=pixel_decode_error,
            )
        )
    return out
