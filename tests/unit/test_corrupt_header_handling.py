"""A corrupt DICOM dataset quarantines that one instance without aborting the rest of
the batch (STAGES.md S2: "Fails on: corrupt DICOM header, unreadable dataset" -- per
instance, not per batch). Requires a real Postgres/MinIO -- gated behind
PIPELINE_INTEGRATION_TESTS even though it lives under tests/unit/ per the plan's file
layout -- see tests/fixtures/shared.py.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.common.config import get_settings
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from tests.fixtures.dicom_factory import build_dicom_bytes
from tests.fixtures.shared import integration


@integration
def test_corrupt_dataset_quarantines_without_aborting_siblings(
    pipeline_session, bronze_minio_client, tmp_path: Path
) -> None:
    settings = get_settings()
    good = build_dicom_bytes()
    # Passes the S1 DICM-magic check (correct bytes at offset 128) but is not a parseable
    # DICOM dataset beyond that -- pydicom.dcmread must raise on it.
    corrupt = b"\x00" * 128 + b"DICM" + b"\xff" * 4 + b"not a real file-meta group"

    source_dir = tmp_path / "batch"
    source_dir.mkdir()
    (source_dir / "good.dcm").write_bytes(good)
    (source_dir / "bad.dcm").write_bytes(corrupt)

    batch = ingest(
        pipeline_session, bronze_minio_client, settings, "corrupt-batch", str(source_dir)
    )
    assert batch.file_count == 2  # both passed the S1 magic check

    result = parse_and_index_batch(pipeline_session, bronze_minio_client, settings, batch.batch_id)

    quarantined = [r for r in result.indexed if r.quarantined]
    succeeded = [r for r in result.indexed if not r.quarantined]
    assert len(quarantined) == 1
    assert len(succeeded) == 1
    assert quarantined[0].reason_code in ("S2_CORRUPT_HEADER", "S2_UNREADABLE_DATASET")
