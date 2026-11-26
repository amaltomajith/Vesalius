"""TESTING.md Proof 4, through the REAL publish -> serve -> consume path.

`test_contract_violation.py` proves the consumer's validation on a locally built contract dict.
DATA-MODEL.md's demonstration is stronger: "publish v1.1 with `coordinate_frame` changed to `LPS`,
show the consumer reject it at load rather than silently producing mirrored results." Here v1.1 is
published with `publish_contract`, fetched with the same query the API serves, and handed to the
consumer together with a REAL gold volume produced by the pipeline.

The publish happens inside the test's own transaction, which is rolled back: publishing v1.1 for
real would make it the "current" contract and change `contract_version` in every API response.
That isolation is itself asserted (a separate connection must still see only 1.0).

Why this matters, for the write-up: a left-right flip from an unnoticed orientation change is a
real and serious class of medical-imaging bug. The array looks fine, every statistic is unchanged,
and results are anatomically mirrored. The contract makes the coordinate-frame guarantee
machine-checkable instead of a sentence in a README.
"""

from __future__ import annotations

import copy
import gzip
import struct

import pytest
from sqlalchemy import select

from consumer.contract import ContractViolation
from consumer.loader import load
from pipeline.catalog.contracts import (
    VOLUME_CONTRACT_V1_0,
    ContractConflictError,
    publish_all,
    publish_contract,
)
from pipeline.catalog.models import Artifact
from pipeline.catalog.session import get_serving_session
from pipeline.common.config import get_settings
from pipeline.stages.s11_export.service import export_series
from pipeline.stages.s12_serve import queries
from pipeline.storage.object_store import get_bytes
from tests.fixtures.shared import integration
from tests.integration.test_s11_export_pipeline import _head_through_s10


def _v1_1_lps() -> dict:  # type: ignore[type-arg]
    contract = copy.deepcopy(VOLUME_CONTRACT_V1_0)
    contract["version"] = "1.1"
    contract["spatial"]["coordinate_frame"] = "LPS"
    return contract


def _gold_volume(session, silver, gold, settings) -> bytes:  # type: ignore[no-untyped-def]
    sid = _head_through_s10(session, silver, settings)
    assert not export_series(session, silver, gold, settings, sid).skipped
    art = session.execute(
        select(Artifact).where(
            Artifact.series_id == sid, Artifact.zone == "gold", Artifact.kind == "volume_nifti"
        )
    ).scalar_one()
    return get_bytes(gold, settings.minio_gold_bucket, art.object_key)


@integration
def test_a_published_lps_contract_is_rejected_by_the_consumer_at_load(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    settings = get_settings()
    volume = _gold_volume(pipeline_session, silver_minio_client, gold_minio_client, settings)

    publish_all(pipeline_session)  # v1.0 (idempotent)
    assert publish_contract(pipeline_session, _v1_1_lps()) is True

    served_11 = queries.get_contract(pipeline_session, "volume_contract", "1.1")
    served_10 = queries.get_contract(pipeline_session, "volume_contract", "1.0")
    assert served_11 is not None and served_10 is not None
    assert served_11.schema_["spatial"]["coordinate_frame"] == "LPS"

    # control: the SAME volume loads fine under the contract it was produced under...
    assert load(volume, served_10.schema_).array.ndim == 3
    # ...and is refused under the drifted one -- refused, not warned about
    with pytest.raises(ContractViolation, match="RAS"):
        load(volume, served_11.schema_)

    # published versions are immutable: v1.1 cannot be quietly edited afterwards
    edited = _v1_1_lps()
    edited["spatial"]["coordinate_frame"] = "RAS"
    with pytest.raises(ContractConflictError):
        publish_contract(pipeline_session, edited)

    # isolation: the publish above is uncommitted, so no other connection (the API's) sees v1.1
    with get_serving_session(settings) as other:
        assert queries.get_contract(other, "volume_contract", "1.1") is None
        assert queries.current_contract_version(other) == "1.0"


@integration
def test_a_real_gold_volume_with_a_flipped_orientation_is_rejected_even_under_v1_0(
    pipeline_session, silver_minio_client, gold_minio_client
) -> None:
    """The other half of the claim: even when the CONTRACT is right, a volume whose header has
    been flipped (a reflected sform) is refused -- the consumer checks the data, not just the
    paperwork."""
    settings = get_settings()
    volume = _gold_volume(pipeline_session, silver_minio_client, gold_minio_client, settings)
    publish_all(pipeline_session)
    contract = queries.get_contract(pipeline_session, "volume_contract", "1.0")
    assert contract is not None

    raw = bytearray(gzip.decompress(volume))
    for off in (280, 296, 312):  # negate the sform's x column: a left-right mirror
        (v,) = struct.unpack_from("<f", raw, off)
        struct.pack_into("<f", raw, off, -v)
    with pytest.raises(ContractViolation, match="mirrored"):
        load(gzip.compress(bytes(raw), mtime=0), contract.schema_)
