"""S12's read-only catalog queries. Every function takes the SERVING session (`serving_ro`: SELECT
on the public tables, no grant of any kind on `restricted`) and never writes.

Gold-only is enforced here at the query level: artifacts are only ever selected with
`zone == SERVED_ZONE`, and this module names no artifact kind (it filters by zone, never by kind).
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pipeline.catalog.lineage import provenance, stage_number
from pipeline.catalog.models import (
    Artifact,
    DataContract,
    Instance,
    Quarantine,
    Series,
    Study,
    TransformRun,
)
from pipeline.stages.s12_serve import params
from pipeline.stages.s12_serve.models import (
    ArtifactOut,
    ContractResponse,
    LineageEdgeOut,
    LineageNodeOut,
    LineageResponse,
    QualityReport,
    QuarantineEntry,
    SeriesDetail,
    SeriesSummary,
    StageSummary,
)

_SAFE_REASON = re.compile(params.SAFE_REASON_PATTERN)


class NoContractPublishedError(Exception):
    """No contract is published, so no data response can carry a contract version (fail closed)."""


def safe_reason(reason: str | None) -> str | None:
    """A reason CODE passes through; free text (an exception message) is replaced -- see
    params.SAFE_REASON_PATTERN."""
    if reason is None:
        return None
    return reason if _SAFE_REASON.fullmatch(reason) else params.REDACTED


def serve_parameters(stage: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """What of a run's `parameters` may leave the service -- see params.INPUT_DERIVED_STAGES_MAX."""
    if stage_number(stage) > params.INPUT_DERIVED_STAGES_MAX:
        return parameters
    return {k: v for k, v in parameters.items() if k.endswith(params.SERVED_KEY_SUFFIX_EARLY)}


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def current_contract_version(session: Session) -> str:
    versions = list(
        session.execute(
            select(DataContract.version).where(DataContract.name == params.CONTRACT_NAME)
        ).scalars()
    )
    if not versions:
        raise NoContractPublishedError(params.CONTRACT_NAME)
    return max(versions, key=_version_key)


def get_contract(session: Session, name: str, version: str | None) -> ContractResponse | None:
    stmt = select(DataContract).where(DataContract.name == name)
    if version is not None:
        stmt = stmt.where(DataContract.version == version)
    rows = list(session.execute(stmt).scalars())
    if not rows:
        return None
    row = max(rows, key=lambda r: _version_key(r.version))
    return ContractResponse(
        contract_version=row.version,
        name=row.name,
        version=row.version,
        schema=row.schema,
        published_at=row.published_at,
    )


def _has_gold_subquery():  # type: ignore[no-untyped-def]
    return select(Artifact.series_id).where(Artifact.zone == params.SERVED_ZONE).distinct()


def search_series(
    session: Session,
    *,
    modality: str | None,
    body_part: str | None,
    quality_status: str | None,
    has_gold: bool | None,
    limit: int,
    offset: int,
) -> tuple[int, list[SeriesSummary]]:
    gold_ids = _has_gold_subquery()
    stmt = select(Series, Study.body_part).join(Study, Study.id == Series.study_id)
    if modality is not None:
        stmt = stmt.where(Series.modality == modality)
    if body_part is not None:
        stmt = stmt.where(Study.body_part == body_part)
    if quality_status is not None:
        stmt = stmt.where(Series.quality_status == quality_status)
    if has_gold is True:
        stmt = stmt.where(Series.id.in_(gold_ids))
    elif has_gold is False:
        stmt = stmt.where(Series.id.not_in(gold_ids))
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(
        stmt.order_by(Series.created_at.desc(), Series.id).limit(limit).offset(offset)
    ).all()
    gold_present = set(
        session.execute(
            select(Artifact.series_id).where(
                Artifact.zone == params.SERVED_ZONE, Artifact.series_id.in_([r[0].id for r in rows])
            )
        ).scalars()
    )
    items = [
        SeriesSummary(
            id=s.id,
            pseudo_series_uid=s.pseudo_series_uid,
            modality=s.modality,
            body_part=bp,
            quality_status=s.quality_status,
            instance_count=s.instance_count,
            has_gold=s.id in gold_present,
        )
        for s, bp in rows
    ]
    return total, items


def _has_gold(session: Session, series_id: UUID) -> bool:
    return (
        session.execute(
            select(func.count())
            .select_from(Artifact)
            .where(Artifact.series_id == series_id, Artifact.zone == params.SERVED_ZONE)
        ).scalar_one()
        > 0
    )


def get_series(session: Session, series_id: UUID, contract_version: str) -> SeriesDetail | None:
    row = session.execute(
        select(Series, Study).join(Study, Study.id == Series.study_id).where(Series.id == series_id)
    ).first()
    if row is None:
        return None
    s, study = row
    return SeriesDetail(
        contract_version=contract_version,
        id=s.id,
        pseudo_series_uid=s.pseudo_series_uid,
        modality=s.modality,
        body_part=study.body_part,
        deface_required=study.deface_required,
        quality_status=s.quality_status,
        instance_count=s.instance_count,
        rows=s.rows,
        columns=s.columns,
        pixel_spacing=s.pixel_spacing,
        slice_spacing=s.slice_spacing,
        spacing_uniform=s.spacing_uniform,
        has_gold=_has_gold(session, series_id),
    )


def series_exists(session: Session, series_id: UUID) -> bool:
    return session.get(Series, series_id) is not None


def _artifact_out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id,
        series_id=a.series_id,
        kind=a.kind,
        format=a.format,
        sha256=a.sha256,
        bytes=a.bytes,
        contract_version=a.contract_version,
        created_at=a.created_at,
    )


def gold_artifacts(session: Session, series_id: UUID) -> list[ArtifactOut]:
    rows = session.execute(
        select(Artifact)
        .where(Artifact.series_id == series_id, Artifact.zone == params.SERVED_ZONE)
        .order_by(Artifact.kind)
    ).scalars()
    return [_artifact_out(a) for a in rows]


def get_gold_artifact(session: Session, artifact_id: UUID) -> tuple[ArtifactOut, str] | None:
    """(metadata, object_key) -- or None for a missing OR non-gold artifact (indistinguishable to
    the caller, so a silver artifact's existence is never confirmed)."""
    a = session.execute(
        select(Artifact).where(Artifact.id == artifact_id, Artifact.zone == params.SERVED_ZONE)
    ).scalar_one_or_none()
    return None if a is None else (_artifact_out(a), a.object_key)


STRUCTURES_MANIFEST_SUFFIX = "/mesh/structures.json"
GROUP_KEY_RE = re.compile(r"^[a-z]+$")


def structure_studies(session: Session) -> list[dict[str, Any]]:
    """Series that have a structures manifest in gold, newest first, with study facts."""
    rows = session.execute(
        select(Artifact, Series, Study)
        .join(Series, Series.id == Artifact.series_id)
        .join(Study, Study.id == Series.study_id)
        .where(
            Artifact.zone == params.SERVED_ZONE,
            Artifact.object_key.endswith(STRUCTURES_MANIFEST_SUFFIX),
        )
        .order_by(Artifact.created_at.desc())
    ).all()
    out = []
    for art, series, study in rows:
        thumb = session.execute(
            select(Artifact.id).where(
                Artifact.zone == params.SERVED_ZONE,
                Artifact.series_id == series.id,
                Artifact.object_key == f"{series.id}/thumbnail.png",
            )
        ).scalar_one_or_none()
        out.append(
            {
                "id": str(series.id),
                "modality": study.modality,
                "body_part": study.body_part,
                "shifted_study_date": (
                    study.shifted_study_date.isoformat() if study.shifted_study_date else None
                ),
                "slice_count": series.instance_count,
                "manifest_bytes": art.bytes,
                "thumbnail_artifact_id": str(thumb) if thumb else None,
            }
        )
    return out


def structures_manifest_key(session: Session, series_id: UUID) -> str | None:
    key = f"{series_id}{STRUCTURES_MANIFEST_SUFFIX}"
    a = session.execute(
        select(Artifact).where(
            Artifact.zone == params.SERVED_ZONE,
            Artifact.series_id == series_id,
            Artifact.object_key == key,
        )
    ).scalar_one_or_none()
    return None if a is None else a.object_key


def mesh_group_key(session: Session, series_id: UUID, group: str) -> str | None:
    if not GROUP_KEY_RE.match(group):
        return None
    key = f"{series_id}/mesh/{group}.glb"
    a = session.execute(
        select(Artifact).where(
            Artifact.zone == params.SERVED_ZONE,
            Artifact.series_id == series_id,
            Artifact.object_key == key,
        )
    ).scalar_one_or_none()
    return None if a is None else a.object_key


def series_lineage(
    session: Session, series_id: UUID, contract_version: str
) -> LineageResponse | None:
    """Provenance of the series' GOLD artifacts. None when the series has none (nothing servable
    to trace)."""
    roots = list(
        session.execute(
            select(Artifact.produced_by_run)
            .where(Artifact.series_id == series_id, Artifact.zone == params.SERVED_ZONE)
            .distinct()
        ).scalars()
    )
    if not roots:
        return None
    graph = provenance(session, roots)
    return LineageResponse(
        contract_version=contract_version,
        series_id=series_id,
        root_runs=sorted(roots, key=str),
        nodes=[
            LineageNodeOut(
                run_id=n.run_id,
                stage=n.stage,
                stage_version=n.stage_version,
                status=n.status,
                depth=n.depth,
                parameters=serve_parameters(n.stage, n.parameters),
            )
            for n in graph.nodes
        ],
        edges=[LineageEdgeOut(downstream_run=d, upstream_run=u) for d, u in graph.edges],
    )


def quality_report(
    session: Session, series_id: UUID, contract_version: str
) -> QualityReport | None:
    s = session.get(Series, series_id)
    if s is None:
        return None
    instance_ids = list(
        session.execute(select(Instance.id).where(Instance.series_id == series_id)).scalars()
    )
    subjects = [series_id, *instance_ids]
    runs = list(
        session.execute(
            select(TransformRun)
            .where(TransformRun.subject_id.in_(subjects))
            .order_by(TransformRun.started_at)
        ).scalars()
    )
    by_stage: dict[str, list[TransformRun]] = {}
    for r in runs:
        by_stage.setdefault(r.stage, []).append(r)
    stages = []
    for stage in sorted(by_stage, key=stage_number):
        rs = by_stage[stage]
        counts: dict[str, int] = {}
        for r in rs:
            counts[r.status] = counts.get(r.status, 0) + 1
        last = rs[-1]
        stages.append(
            StageSummary(
                stage=stage,
                run_count=len(rs),
                by_status=counts,
                latest_status=last.status,
                latest_reason=safe_reason(last.reason),
            )
        )
    quarantined = session.execute(
        select(Quarantine)
        .where(Quarantine.subject_id.in_(subjects))
        .order_by(Quarantine.created_at)
    ).scalars()
    return QualityReport(
        contract_version=contract_version,
        series_id=series_id,
        quality_status=s.quality_status,
        instance_count=s.instance_count,
        rows=s.rows,
        columns=s.columns,
        pixel_spacing=s.pixel_spacing,
        slice_spacing=s.slice_spacing,
        spacing_uniform=s.spacing_uniform,
        stages=stages,
        quarantine=[
            QuarantineEntry(
                stage=q.stage,
                reason_code=safe_reason(q.reason_code) or params.REDACTED,
                created_at=q.created_at,
            )
            for q in quarantined
        ],
    )
