"""S12 over real HTTP-shaped calls (FastAPI TestClient) against live Postgres + MinIO.

Data is built once per module through the REAL S1->S11 code and COMMITTED (the API reads through a
separate `serving_ro` connection, which cannot see uncommitted rows). Assertions scope to the
series created here, never global counts.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from api.dependencies import serving_session_dependency
from api.main import create_app
from pipeline.catalog.contracts import publish_all
from pipeline.catalog.models import (
    Artifact,
    Instance,
    Quarantine,
    Series,
    Study,
    TransformRun,
)
from pipeline.catalog.session import get_pipeline_session
from pipeline.common.config import get_settings
from pipeline.stages.s11_export.service import export_series
from pipeline.storage.object_store import bronze_client, gold_client, silver_client
from tests.fixtures.shared import integration, require_integration
from tests.integration.test_s11_export_pipeline import _chest_through_s10, _head_through_s10

GOLD_KINDS = {"surface_glb", "volume_nifti", "thumbnail", "manifest"}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


@dataclass
class Served:
    series_id: UUID
    gold_ids: dict[str, UUID]
    silver_artifact_id: UUID


def _collect(session, sid: UUID) -> Served:  # type: ignore[no-untyped-def]
    arts = list(session.execute(select(Artifact).where(Artifact.series_id == sid)).scalars())
    gold = {a.kind: a.id for a in arts if a.zone == "gold"}
    silver = next(a.id for a in arts if a.zone == "silver")
    return Served(sid, gold, silver)


@pytest.fixture(scope="module")
def contracts_published():  # type: ignore[no-untyped-def]
    require_integration()
    with get_pipeline_session(get_settings()) as session:
        publish_all(session)


@pytest.fixture(scope="module")
def served_head(contracts_published):  # type: ignore[no-untyped-def]
    settings = get_settings()
    silver, gold = silver_client(settings), gold_client(settings)
    with get_pipeline_session(settings) as session:
        sid = _head_through_s10(session, silver, settings)
        export_series(session, silver, gold, settings, sid)
        return _collect(session, sid)


@pytest.fixture(scope="module")
def unserved_head(contracts_published):  # type: ignore[no-untyped-def]
    """Through S10 only: a series that exists in the catalog but has no gold artifact."""
    settings = get_settings()
    with get_pipeline_session(settings) as session:
        return _head_through_s10(session, silver_client(settings), settings)


@dataclass
class ServedChest:
    served: Served
    originals: set[str]
    filenames: set[str]


@pytest.fixture(scope="module")
def served_chest(contracts_published):  # type: ignore[no-untyped-def]
    settings = get_settings()
    bronze, silver, gold = (
        bronze_client(settings),
        silver_client(settings),
        gold_client(settings),
    )
    with (
        mock.patch(
            "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions", lambda i: []
        ),
        mock.patch("pipeline.stages.s5_verify.pixel_verify.detect_text_regions", lambda i: []),
        tempfile.TemporaryDirectory() as tmp,
        get_pipeline_session(settings) as session,
    ):
        sid, _ = _chest_through_s10(session, bronze, silver, settings, Path(tmp), "api.zip")
        export_series(session, silver, gold, settings, sid, "soft_tissue")
        series = session.get(Series, sid)
        study = session.get(Study, series.study_id)  # type: ignore[union-attr]
        pseudos = [series.pseudo_series_uid, study.pseudo_study_uid]  # type: ignore[union-attr]
        pseudos += [
            i.pseudo_sop_uid
            for i in session.execute(select(Instance).where(Instance.series_id == sid)).scalars()
        ]
        rows = session.execute(
            text("select original_uid from restricted.uid_map where pseudo_uid = any(:p)"),
            {"p": pseudos},
        ).all()
        originals = {r[0] for r in rows}
        pid = session.execute(
            text(
                "select original_patient_id from restricted.patient_map "
                "where pseudo_patient_id = :p"
            ),
            {"p": study.pseudo_patient_id},  # type: ignore[union-attr]
        ).scalar()
        if pid:
            originals.add(pid)
        return ServedChest(_collect(session, sid), originals, {f"scan{i}.dcm" for i in range(10)})


@pytest.fixture(scope="module")
def client(contracts_published) -> TestClient:  # type: ignore[no-untyped-def]
    return TestClient(create_app())


# ---- contracts --------------------------------------------------------------------------------


@integration
def test_the_published_contract_is_served_latest_and_by_version(client: TestClient) -> None:
    latest = client.get("/contracts/volume_contract")
    assert latest.status_code == 200
    body = latest.json()
    assert body["version"] == body["contract_version"] == "1.0"
    assert body["schema"]["spatial"]["coordinate_frame"] == "RAS"
    assert client.get("/contracts/volume_contract/1.0").json() == body
    assert client.get("/contracts/volume_contract/9.9").status_code == 404
    assert client.get("/contracts/no_such_contract").status_code == 404


# ---- catalog search ---------------------------------------------------------------------------


@integration
def test_search_filters_and_reports_the_contract_version(
    client: TestClient, served_head: Served, unserved_head: UUID
) -> None:
    r = client.get(
        "/series",
        params={
            "has_gold": "true",
            "modality": "CT",
            "quality_status": "pass",
            "body_part": "HEAD",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["contract_version"] == "1.0"
    ids = {i["id"] for i in body["items"]}
    assert str(served_head.series_id) in ids and str(unserved_head) not in ids
    assert all(i["has_gold"] and i["modality"] == "CT" for i in body["items"])

    no_gold = client.get("/series", params={"has_gold": "false", "body_part": "HEAD"}).json()
    no_gold_ids = {i["id"] for i in no_gold["items"]}
    assert str(unserved_head) in no_gold_ids and str(served_head.series_id) not in no_gold_ids
    assert all(i["has_gold"] is False for i in no_gold["items"])
    # the flag itself, not just the filter: a series with silver artifacts but no gold is False
    unserved_item = next(i for i in no_gold["items"] if i["id"] == str(unserved_head))
    assert unserved_item["has_gold"] is False

    assert client.get("/series", params={"modality": "ZZ"}).json()["items"] == []


@integration
def test_search_paginates_and_rejects_bad_bounds(client: TestClient, served_head: Served) -> None:
    first = client.get("/series", params={"limit": 1, "offset": 0}).json()
    second = client.get("/series", params={"limit": 1, "offset": 1}).json()
    assert first["total"] == second["total"] >= 2
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get("/series", params=bad).status_code == 422


@integration
def test_search_items_expose_only_the_summary_fields(
    client: TestClient, served_head: Served
) -> None:
    item = client.get("/series", params={"has_gold": "true", "limit": 1}).json()["items"][0]
    assert set(item) == {
        "id",
        "pseudo_series_uid",
        "modality",
        "body_part",
        "quality_status",
        "instance_count",
        "has_gold",
    }


@integration
def test_series_detail_reports_no_gold_for_a_series_with_only_silver_artifacts(
    client: TestClient, unserved_head: UUID
) -> None:
    assert client.get(f"/series/{unserved_head}").json()["has_gold"] is False


@integration
def test_series_detail_and_404(client: TestClient, served_head: Served) -> None:
    r = client.get(f"/series/{served_head.series_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["contract_version"] == "1.0" and d["has_gold"] is True and d["deface_required"] is True
    assert "pseudo_patient_id" not in d
    assert client.get(f"/series/{uuid4()}").status_code == 404
    assert client.get("/series/not-a-uuid").status_code == 422


# ---- gold-only artifacts + presigned URLs ------------------------------------------------------


@integration
def test_only_gold_artifacts_are_listed(client: TestClient, served_head: Served) -> None:
    r = client.get(f"/series/{served_head.series_id}/artifacts")
    body = r.json()
    assert r.status_code == 200 and body["contract_version"] == "1.0"
    assert {a["kind"] for a in body["artifacts"]} == GOLD_KINDS
    listed = {a["id"] for a in body["artifacts"]}
    assert str(served_head.silver_artifact_id) not in listed
    assert client.get(f"/series/{uuid4()}/artifacts").status_code == 404


@integration
def test_a_presigned_url_downloads_exactly_the_catalogued_gold_bytes(
    client: TestClient, served_head: Served
) -> None:
    for kind, aid in served_head.gold_ids.items():
        r = client.get(f"/artifacts/{aid}")
        assert r.status_code == 200, kind
        body = r.json()
        assert body["contract_version"] == "1.0" and body["expires_in_seconds"] == 300
        assert "object_key" not in body["artifact"]
        got = httpx.get(body["url"], timeout=30)
        assert got.status_code == 200, kind
        assert _sha(got.content) == body["artifact"]["sha256"]
        assert len(got.content) == body["artifact"]["bytes"]


@integration
def test_a_silver_artifact_is_not_found_and_looks_exactly_like_a_missing_one(
    client: TestClient, served_head: Served
) -> None:
    silver = client.get(f"/artifacts/{served_head.silver_artifact_id}")
    missing = client.get(f"/artifacts/{uuid4()}")
    assert silver.status_code == missing.status_code == 404
    assert silver.json() == missing.json()


@integration
def test_a_presigned_url_cannot_be_redirected_to_silver_or_another_key(
    client: TestClient, served_head: Served
) -> None:
    sid = served_head.series_id
    url = client.get(f"/artifacts/{served_head.gold_ids['thumbnail']}").json()["url"]
    assert f"/gold/{sid}/thumbnail.png" in url
    for tampered in (
        url.replace(f"/gold/{sid}/thumbnail.png", f"/silver/{sid}/volume.nii.gz"),
        url.replace("thumbnail.png", "surface.glb"),
    ):
        assert httpx.get(tampered, timeout=30).status_code == 403


@integration
def test_an_unpublished_contract_fails_closed_with_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipeline.stages.s12_serve import queries

    def none_published(_session: object) -> str:
        raise queries.NoContractPublishedError("volume_contract")

    monkeypatch.setattr("api.routers.catalog.queries.current_contract_version", none_published)
    assert client.get("/series").status_code == 503


# ---- lineage ------------------------------------------------------------------------------------


@integration
def test_lineage_of_the_gold_artifacts_walks_the_real_chain(
    client: TestClient, served_head: Served
) -> None:
    r = client.get(f"/series/{served_head.series_id}/lineage")
    assert r.status_code == 200
    body = r.json()
    assert body["contract_version"] == "1.0"
    depth = {n["stage"]: n["depth"] for n in body["nodes"]}
    assert depth == {"S11": 0, "S10": 1, "S7": 1, "S6": 1, "S9": 2, "S8": 3}
    by_id = {n["run_id"]: n["stage"] for n in body["nodes"]}
    edge_stages = {(by_id[e["downstream_run"]], by_id[e["upstream_run"]]) for e in body["edges"]}
    assert {("S11", "S10"), ("S10", "S9"), ("S9", "S8")} <= edge_stages
    s11 = next(n for n in body["nodes"] if n["stage"] == "S11")
    assert s11["parameters"]["source_parameters"]["segment"]["preset"] == "bone"
    assert s11["parameters"]["deface"]["reason"] == "body_part=HEAD"


@integration
def test_a_series_with_no_gold_has_no_lineage_to_serve(
    client: TestClient, unserved_head: UUID
) -> None:
    assert client.get(f"/series/{unserved_head}/lineage").status_code == 404
    assert client.get(f"/series/{uuid4()}/lineage").status_code == 404


# ---- quality report -----------------------------------------------------------------------------


@integration
def test_the_quality_report_never_returns_quarantine_detail_or_free_text_reasons(
    client: TestClient, unserved_head: UUID
) -> None:
    settings = get_settings()
    with get_pipeline_session(settings) as session:
        session.add(
            Quarantine(
                id=uuid4(),
                subject_type="series",
                subject_id=unserved_head,
                stage="S3",
                reason_code="QG_TOO_FEW_SLICES",
                detail={"failing_value": "DOE^JANE", "patient": "DOE^JANE"},
            )
        )
        session.add(
            TransformRun(
                id=uuid4(),
                stage="S4",
                stage_version="1.0.0",
                idempotency_key=uuid4().hex,
                subject_type="series",
                subject_id=unserved_head,
                parameters={},
                input_hashes=[],
                status="failed",
                reason="ValueError: bad value 'DOE^JANE' in (0010,0010)",
                started_at=datetime.now(UTC),
            )
        )
    r = client.get(f"/series/{unserved_head}/quality")
    assert r.status_code == 200
    body = r.json()
    assert body["contract_version"] == "1.0"
    assert body["quarantine"] == [
        {"stage": "S3", "reason_code": "QG_TOO_FEW_SLICES", "created_at": mock.ANY}
    ]
    s4 = next(s for s in body["stages"] if s["stage"] == "S4")
    assert s4["latest_status"] == "failed" and s4["latest_reason"] == "redacted"
    assert "DOE" not in r.text
    assert client.get(f"/series/{uuid4()}/quality").status_code == 404


@integration
def test_the_quality_report_summarises_every_stage_of_a_real_chain(
    client: TestClient, served_chest: ServedChest
) -> None:
    body = client.get(f"/series/{served_chest.served.series_id}/quality").json()
    stages = {s["stage"]: s for s in body["stages"]}
    assert set(stages) == {f"S{i}" for i in range(1, 12)}
    assert stages["S1"]["run_count"] == 10 and stages["S1"]["by_status"] == {"success": 10}
    assert body["quality_status"] == "pass" and body["quarantine"] == []


# ---- the serving role ---------------------------------------------------------------------------


@integration
def test_the_api_reads_as_serving_ro_and_cannot_touch_restricted_or_write(
    contracts_published,
) -> None:
    gen = serving_session_dependency()
    session = next(gen)
    try:
        assert session.execute(text("select current_user")).scalar() == "serving_ro"
        with pytest.raises(DBAPIError):
            session.execute(text("select count(*) from restricted.uid_map"))
        session.rollback()
        with pytest.raises(DBAPIError):
            session.execute(text("delete from artifacts"))
        session.rollback()
    finally:
        gen.close()


# ---- the leak test ----------------------------------------------------------------------------


def _leaks(payload: str, secrets: set[str]) -> set[str]:
    return {s for s in secrets if s in payload}


@integration
def test_no_original_identifier_or_source_filename_appears_in_any_response(
    client: TestClient, served_chest: ServedChest
) -> None:
    sid = served_chest.served.series_id
    secrets = served_chest.originals | served_chest.filenames
    assert len(served_chest.originals) >= 12  # 10 SOPs + series + study, at least

    payloads = [
        client.get(f"/series/{sid}").text,
        client.get(f"/series/{sid}/artifacts").text,
        client.get(f"/series/{sid}/lineage").text,
        client.get(f"/series/{sid}/quality").text,
        client.get("/series", params={"limit": 200}).text,
        client.get("/series", params={"limit": 200, "has_gold": "true"}).text,
        client.get("/contracts/volume_contract").text,
    ]
    payloads += [
        client.get(f"/artifacts/{aid}").text for aid in served_chest.served.gold_ids.values()
    ]
    manifest_url = json.loads(
        client.get(f"/artifacts/{served_chest.served.gold_ids['manifest']}").text
    )["url"]
    payloads.append(httpx.get(manifest_url, timeout=30).text)  # the served manifest itself

    for p in payloads:
        assert _leaks(p, secrets) == set()


@integration
def test_the_leak_scanner_can_actually_fail(served_chest: ServedChest) -> None:
    secret = next(iter(served_chest.originals))
    assert _leaks(f'{{"x": "{secret}"}}', served_chest.originals) == {secret}
    assert _leaks('{"x": "scan3.dcm"}', served_chest.filenames) == {"scan3.dcm"}


@integration
def test_the_lineage_of_a_real_chain_reaches_ingest_through_the_api(
    client: TestClient, served_chest: ServedChest
) -> None:
    body = client.get(f"/series/{served_chest.served.series_id}/lineage").json()
    assert {n["stage"] for n in body["nodes"]} == {f"S{i}" for i in range(1, 12)}
    s1 = [n for n in body["nodes"] if n["stage"] == "S1"]
    assert len(s1) == 10
    assert all("source_name" not in n["parameters"] for n in s1)  # PHI-bearing key withheld
