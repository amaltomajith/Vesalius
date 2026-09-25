"""S12's pure pieces: reason/parameter scrubbing, presigning, and the contract-version rule."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from pydantic import ValidationError

from pipeline.common.config import Settings
from pipeline.stages.s12_serve import models, params
from pipeline.stages.s12_serve.presign import presign_gold_url
from pipeline.stages.s12_serve.queries import safe_reason, serve_parameters


@pytest.mark.parametrize(
    "reason, expected",
    [
        (None, None),
        ("QG_TOO_FEW_SLICES", "QG_TOO_FEW_SLICES"),
        ("defacing_gate_refused:GATE_NO_S7_RUN", "defacing_gate_refused:GATE_NO_S7_RUN"),
        ("series_has_no_s10_mesh", "series_has_no_s10_mesh"),
        # free text from an exception: could carry a tag value
        ("ValueError: bad tag (0010,0010) value 'DOE^JANE'", params.REDACTED),
        ("PatientName is DOE", params.REDACTED),
        ("x" * 81, params.REDACTED),
        ("", params.REDACTED),
    ],
)
def test_only_reason_codes_leave_the_service(reason: str | None, expected: str | None) -> None:
    assert safe_reason(reason) == expected


def test_early_stage_parameters_are_reduced_to_version_keys() -> None:
    raw = {"source_name": "SMITH_JOHN_CT.dcm", "ingest_batch_id": "b", "profile_version": "1"}
    assert serve_parameters("S1", raw) == {"profile_version": "1"}
    assert serve_parameters("S5", {"verify_profile_version": "1", "note": "x"}) == {
        "verify_profile_version": "1"
    }


def test_later_stage_parameters_pass_through_unchanged() -> None:
    raw = {"params_version": "1", "target_spacing_mm": 1.0, "deface": {"reason": "body_part=HEAD"}}
    assert serve_parameters("S6", raw) == raw
    assert serve_parameters("S11", raw) == raw


def test_the_stage_boundary_matches_the_constant() -> None:
    assert params.INPUT_DERIVED_STAGES_MAX == 5  # S1-S5 are the per-instance, input-derived stages


def _settings(**kw: object) -> Settings:
    return Settings(
        minio_endpoint="minio:9000",
        minio_gold_access_key="gold-access",
        minio_gold_secret_key="gold-secret-key-123",
        **kw,  # type: ignore[arg-type]
    )


def test_a_presigned_url_targets_the_gold_bucket_and_expires() -> None:
    url, ttl = presign_gold_url(_settings(presign_expiry_seconds=120), "abc/surface.glb")
    u = urlparse(url)
    q = parse_qs(u.query)
    assert u.path == "/gold/abc/surface.glb"
    assert ttl == 120 and q["X-Amz-Expires"] == ["120"]
    assert q["X-Amz-Credential"][0].startswith("gold-access/")


def test_the_url_is_signed_for_the_public_endpoint_when_configured() -> None:
    internal, _ = presign_gold_url(_settings(), "k")
    public, _ = presign_gold_url(_settings(minio_public_endpoint="localhost:9000"), "k")
    assert urlparse(internal).netloc == "minio:9000"
    assert urlparse(public).netloc == "localhost:9000"
    assert (
        parse_qs(urlparse(internal).query)["X-Amz-Signature"]
        != parse_qs(urlparse(public).query)["X-Amz-Signature"]
    )  # the host is part of what is signed


def test_the_signing_key_never_appears_in_the_url() -> None:
    url, _ = presign_gold_url(_settings(), "k")
    assert "gold-secret-key-123" not in url


DATA_MODELS = [
    models.SeriesListResponse,
    models.SeriesDetail,
    models.ArtifactListResponse,
    models.ArtifactDownload,
    models.LineageResponse,
    models.QualityReport,
    models.ContractResponse,
]


@pytest.mark.parametrize("model", DATA_MODELS, ids=lambda m: m.__name__)
def test_every_data_carrying_response_requires_a_contract_version(model: type) -> None:
    field = model.model_fields["contract_version"]
    assert field.is_required()


def test_a_response_without_a_contract_version_fails_validation() -> None:
    with pytest.raises(ValidationError):
        models.ArtifactListResponse(series_id=uuid4(), artifacts=[])  # type: ignore[call-arg]


def test_the_contract_response_serialises_its_schema_under_the_plain_name() -> None:
    r = models.ContractResponse(
        contract_version="1.0",
        name="volume_contract",
        version="1.0",
        schema={"a": 1},
        published_at=datetime.now(UTC),
    )
    assert r.model_dump(by_alias=True)["schema"] == {"a": 1}


def test_no_response_model_exposes_a_patient_identifier_or_an_object_key() -> None:
    forbidden = {"pseudo_patient_id", "object_key", "content_hash", "shifted_study_date", "detail"}
    for m in [*DATA_MODELS, models.SeriesSummary, models.ArtifactOut, models.QuarantineEntry]:
        assert forbidden.isdisjoint(m.model_fields), (m.__name__, forbidden & set(m.model_fields))


class _Scalars:
    def __init__(self, rows: list) -> None:  # type: ignore[type-arg]
        self._rows = rows

    def scalars(self) -> list:  # type: ignore[type-arg]
        return self._rows


class _ContractSession:
    def __init__(self, versions: list[str]) -> None:
        self.versions = versions

    def execute(self, _stmt: object) -> _Scalars:
        return _Scalars(self.versions)


def test_the_current_contract_is_the_numerically_highest_version_not_the_lexical_one() -> None:
    from pipeline.stages.s12_serve.queries import current_contract_version

    # "1.10" > "1.2" numerically, "1.2" > "1.10" lexically; "2.0" is highest of all
    assert current_contract_version(_ContractSession(["1.2", "1.10", "1.9"])) == "1.10"  # type: ignore[arg-type]
    assert current_contract_version(_ContractSession(["1.0", "2.0", "1.10"])) == "2.0"  # type: ignore[arg-type]


def test_no_published_contract_fails_closed() -> None:
    from pipeline.stages.s12_serve.queries import NoContractPublishedError, current_contract_version

    with pytest.raises(NoContractPublishedError):
        current_contract_version(_ContractSession([]))  # type: ignore[arg-type]
