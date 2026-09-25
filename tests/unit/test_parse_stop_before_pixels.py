"""Confirms S2 reads with `pydicom.dcmread(path, stop_before_pixels=True)` (STAGES.md S2)
and never touches pixel data. Requires a real Postgres/MinIO -- gated behind
PIPELINE_INTEGRATION_TESTS even though it lives under tests/unit/ per the plan's file
layout -- see tests/fixtures/shared.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pydicom

from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index import service as s2_service
from tests.fixtures.dicom_factory import build_dicom_bytes
from tests.fixtures.shared import integration


@integration
def test_dcmread_called_with_stop_before_pixels_true(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    source_dir = tmp_path / "batch"
    source_dir.mkdir()
    (source_dir / "scan.dcm").write_bytes(build_dicom_bytes())

    batch = ingest(pipeline_session, bronze_minio_client, settings, "spy-batch", str(source_dir))

    original_dcmread = pydicom.dcmread
    calls: list[dict[str, object]] = []

    def spy_dcmread(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return original_dcmread(*args, **kwargs)  # type: ignore[arg-type]

    with patch.object(s2_service.pydicom, "dcmread", side_effect=spy_dcmread):
        result = s2_service.parse_and_index_batch(
            pipeline_session, bronze_minio_client, settings, batch.batch_id
        )

    assert len(calls) == 1
    assert calls[0].get("stop_before_pixels") is True
    # our synthetic fixture carries no PixelData element at all -- if S2 ever touched
    # pixel_array, dcmread/pixel decode would raise and this instance would quarantine
    # instead of index successfully.
    assert result.indexed[0].quarantined is False
