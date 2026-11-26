"""S12 -- serving routes. Thin wrappers over `pipeline.stages.s12_serve`. Every route reads through
the read-only `serving_ro` session and serves artifacts from gold only; this module never imports a
silver/bronze/quarantine client or the pipeline (write) session -- enforced by
tests/unit/test_s12_static_architecture.py.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from api.dependencies import serving_session_dependency, settings_dependency
from pipeline.common.config import Settings
from pipeline.stages.s12_serve import params, queries
from pipeline.stages.s12_serve.models import (
    ArtifactDownload,
    ArtifactListResponse,
    ContractResponse,
    LineageResponse,
    QualityReport,
    SeriesDetail,
    SeriesListResponse,
)
from pipeline.stages.s12_serve.presign import presign_gold_url

router = APIRouter()

ServingSession = Annotated[Session, Depends(serving_session_dependency)]


def _contract_version(session: Session) -> str:
    try:
        return queries.current_contract_version(session)
    except queries.NoContractPublishedError as exc:
        # fail closed: a data response without a contract version would break the S12 contract
        raise HTTPException(
            status_code=503,
            detail="no data contract is published (run `pipeline publish-contracts`)",
        ) from exc


@router.get("/series", response_model=SeriesListResponse)
def search_series(
    session: ServingSession,
    modality: str | None = None,
    body_part: str | None = None,
    quality_status: str | None = None,
    has_gold: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=params.MAX_LIMIT)] = params.DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SeriesListResponse:
    version = _contract_version(session)
    total, items = queries.search_series(
        session,
        modality=modality,
        body_part=body_part,
        quality_status=quality_status,
        has_gold=has_gold,
        limit=limit,
        offset=offset,
    )
    return SeriesListResponse(
        contract_version=version, total=total, limit=limit, offset=offset, items=items
    )


@router.get("/series/{series_id}", response_model=SeriesDetail)
def get_series(series_id: UUID, session: ServingSession) -> SeriesDetail:
    detail = queries.get_series(session, series_id, _contract_version(session))
    if detail is None:
        raise HTTPException(status_code=404, detail="series not found")
    return detail


@router.get("/series/{series_id}/artifacts", response_model=ArtifactListResponse)
def list_artifacts(series_id: UUID, session: ServingSession) -> ArtifactListResponse:
    version = _contract_version(session)
    if not queries.series_exists(session, series_id):
        raise HTTPException(status_code=404, detail="series not found")
    return ArtifactListResponse(
        contract_version=version,
        series_id=series_id,
        artifacts=queries.gold_artifacts(session, series_id),
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDownload)
def get_artifact(
    artifact_id: UUID,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> ArtifactDownload:
    version = _contract_version(session)
    found = queries.get_gold_artifact(session, artifact_id)
    if found is None:
        # missing and non-gold are indistinguishable on purpose
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact, object_key = found
    url, ttl = presign_gold_url(settings, object_key)
    return ArtifactDownload(
        contract_version=version, artifact=artifact, url=url, expires_in_seconds=ttl
    )


@router.get("/series/{series_id}/lineage", response_model=LineageResponse)
def get_lineage(series_id: UUID, session: ServingSession) -> LineageResponse:
    version = _contract_version(session)
    if not queries.series_exists(session, series_id):
        raise HTTPException(status_code=404, detail="series not found")
    lineage = queries.series_lineage(session, series_id, version)
    if lineage is None:
        raise HTTPException(status_code=404, detail="series has no servable artifacts to trace")
    return lineage


@router.get("/series/{series_id}/quality", response_model=QualityReport)
def get_quality(series_id: UUID, session: ServingSession) -> QualityReport:
    report = queries.quality_report(session, series_id, _contract_version(session))
    if report is None:
        raise HTTPException(status_code=404, detail="series not found")
    return report


@router.get("/contracts/{name}", response_model=ContractResponse, response_model_by_alias=True)
def get_latest_contract(name: str, session: ServingSession) -> ContractResponse:
    contract = queries.get_contract(session, name, None)
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return contract


@router.get(
    "/contracts/{name}/{version}", response_model=ContractResponse, response_model_by_alias=True
)
def get_contract(name: str, version: str, session: ServingSession) -> ContractResponse:
    contract = queries.get_contract(session, name, version)
    if contract is None:
        raise HTTPException(status_code=404, detail="contract not found")
    return contract


# ---- studies: the viewer's view of structures-mode output (refs/INTEGRATION.md §6) ---------------
# Same trust boundary as everything above: serving_ro session, gold only, and objects are handed
# out as short-lived presigned redirects ("presigned redirect, gold only" -- refs §6), never
# streamed from the object store by this router.


@router.get("/studies")
def list_studies(session: ServingSession) -> dict[str, object]:
    return {
        "contract_version": _contract_version(session),
        "mesh_contract_version": "1.0",
        "studies": queries.structure_studies(session),
    }


def _redirect(settings: Settings, object_key: str | None, what: str) -> RedirectResponse:
    if object_key is None:
        # missing and non-gold are indistinguishable on purpose
        raise HTTPException(status_code=404, detail=f"{what} not found")
    url, _ttl = presign_gold_url(settings, object_key)
    return RedirectResponse(url, status_code=307)


@router.get("/studies/{series_id}/structures")
def get_study_structures(
    series_id: UUID,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> RedirectResponse:
    return _redirect(
        settings, queries.structures_manifest_key(session, series_id), "structures manifest"
    )


@router.get("/studies/{series_id}/mesh/{group}.glb")
def get_study_mesh(
    series_id: UUID,
    group: str,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> RedirectResponse:
    return _redirect(settings, queries.mesh_group_key(session, series_id, group), "mesh group")


@router.get("/studies/{series_id}/lineage", response_model=LineageResponse)
def get_study_lineage(series_id: UUID, session: ServingSession) -> LineageResponse:
    return get_lineage(series_id, session)
