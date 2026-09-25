"""Helpers for S6 tests: run the REAL `_assemble` / `_gather_silver_slices` code against
in-memory stand-ins for MinIO and the catalog, and read a written volume back two
independent ways (its numpy array, and the affine a RAS consumer sees in the raw header).
"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from uuid import UUID, uuid4

import numpy as np
import pydicom
import SimpleITK as sitk  # noqa: N813 -- universal convention for this library

from pipeline.catalog.models import Artifact, Instance, Series
from pipeline.stages.s6_volume_assembly.affine import read_nifti_sform


class _StubResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        return None

    def release_conn(self) -> None:
        return None


class StubSilverClient:
    """Just enough of minio.Minio for storage.object_store.get_bytes/put_bytes."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})

    def get_object(self, bucket: str, key: str) -> _StubResponse:
        return _StubResponse(self.objects[key])

    def put_object(
        self, bucket: str, key: str, data: io.BytesIO, length: int, content_type: str
    ) -> None:
        self.objects[key] = data.read()


@dataclass
class InMemorySeries:
    series: Series
    artifacts_by_instance: dict[UUID, Artifact]
    client: StubSilverClient


def make_in_memory_series(
    dicom_blobs: list[bytes],
    *,
    modality: str = "CT",
    slice_spacing: float | None = 1.0,
    spacing_uniform: bool | None = True,
) -> InMemorySeries:
    """Builds transient (never-session-added) ORM objects mirroring what S5 leaves behind:
    one Instance + one deid_instance_dicom Artifact per blob, the bytes sitting in a stub
    silver bucket at S5's real key format.
    """
    series = Series(
        id=uuid4(),
        study_id=uuid4(),
        pseudo_series_uid="2.25.1",
        modality=modality,
        instance_count=len(dicom_blobs),
        slice_spacing=slice_spacing,
        spacing_uniform=spacing_uniform,
        quality_status="pass",
    )
    instances: list[Instance] = []
    artifacts: dict[UUID, Artifact] = {}
    client = StubSilverClient()
    for n, blob in enumerate(dicom_blobs):
        ds = pydicom.dcmread(io.BytesIO(blob), stop_before_pixels=True)
        pseudo_sop = str(ds.SOPInstanceUID)
        key = f"{series.id}/instances/{pseudo_sop}.dcm"
        client.objects[key] = blob
        instance = Instance(
            id=uuid4(),
            series_id=series.id,
            pseudo_sop_uid=pseudo_sop,
            content_hash=f"hash-{n}",
            bronze_key=f"bronze-{n}",
        )
        instances.append(instance)
        artifacts[instance.id] = Artifact(
            id=uuid4(),
            series_id=series.id,
            kind="deid_instance_dicom",
            format="dicom",
            zone="silver",
            object_key=key,
            sha256=f"sha-{n}",
            bytes=len(blob),
            produced_by_run=uuid4(),
        )
    series.instances = instances
    return InMemorySeries(series=series, artifacts_by_instance=artifacts, client=client)


@dataclass
class ReadVolume:
    array: np.ndarray  # (z, y, x), via sitk.GetArrayFromImage
    sform: np.ndarray  # 4x4 RAS affine parsed from the RAW header bytes (independent of ITK)
    itk_image: sitk.Image  # ITK's own view (LPS physical space)


def read_volume(volume_bytes: bytes) -> ReadVolume:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "volume.nii.gz")
        with open(path, "wb") as fh:
            fh.write(volume_bytes)
        image = sitk.ReadImage(path)
        return ReadVolume(
            array=sitk.GetArrayFromImage(image),
            sform=read_nifti_sform(path),
            itk_image=image,
        )
