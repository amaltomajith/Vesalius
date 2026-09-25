"""CLI entrypoint. Stage-first invocation -- Dagster/Celery is the intended long-term
caller, so stage logic is called directly here rather than through the HTTP API.
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID

from pipeline.catalog.contracts import publish_all
from pipeline.catalog.lineage import backfill_edges
from pipeline.catalog.session import get_pipeline_session
from pipeline.common.config import get_settings
from pipeline.common.errors import QuarantineError
from pipeline.common.logging import configure_logging
from pipeline.orchestration.deid_pipeline import deidentify_and_verify_batch
from pipeline.stages.s1_ingest.service import ingest
from pipeline.stages.s2_parse_index.service import parse_and_index_batch
from pipeline.stages.s3_quality_gate.service import quality_gate_batch
from pipeline.stages.s6_volume_assembly.service import assemble_volume_batch
from pipeline.stages.s7_deface.service import deface_batch
from pipeline.stages.s8_segment.params import DEFAULT_PRESETS
from pipeline.stages.s8_segment.service import segment_batch
from pipeline.stages.s9_surface_extraction.params import DEFAULT_PRESET as S9_DEFAULT_PRESET
from pipeline.stages.s9_surface_extraction.service import extract_surface_batch
from pipeline.stages.s10_mesh_postprocess.params import DEFAULT_PRESET as S10_DEFAULT_PRESET
from pipeline.stages.s10_mesh_postprocess.params import DEFAULT_TARGET_FACES
from pipeline.stages.s10_mesh_postprocess.service import postprocess_mesh_batch
from pipeline.stages.s11_export.params import DEFAULT_PRESET as S11_DEFAULT_PRESET
from pipeline.stages.s11_export.service import export_batch
from pipeline.storage.object_store import bronze_client, gold_client, silver_client


def _cmd_ingest(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = bronze_client(settings)
    with get_pipeline_session(settings) as session:
        try:
            result = ingest(session, client, settings, source=args.source, path=args.path)
        except QuarantineError as exc:
            print(f"S1 failed: {exc.reason_code} {exc.detail}", file=sys.stderr)
            return 1
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_parse_index(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = bronze_client(settings)
    with get_pipeline_session(settings) as session:
        result = parse_and_index_batch(session, client, settings, batch_id=UUID(args.batch_id))
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_quality_gate(args: argparse.Namespace) -> int:
    settings = get_settings()
    client = bronze_client(settings)
    with get_pipeline_session(settings) as session:
        result = quality_gate_batch(session, client, settings, batch_id=UUID(args.batch_id))
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_deidentify(args: argparse.Namespace) -> int:
    settings = get_settings()
    bronze = bronze_client(settings)
    silver = silver_client(settings)
    with get_pipeline_session(settings) as session:
        result = deidentify_and_verify_batch(
            session, bronze, silver, settings, batch_id=UUID(args.batch_id)
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_assemble_volume(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    with get_pipeline_session(settings) as session:
        result = assemble_volume_batch(session, silver, settings, batch_id=UUID(args.batch_id))
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_deface(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    with get_pipeline_session(settings) as session:
        result = deface_batch(session, silver, settings, batch_id=UUID(args.batch_id))
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_segment(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    presets = tuple(args.preset) if args.preset else DEFAULT_PRESETS
    with get_pipeline_session(settings) as session:
        result = segment_batch(
            session, silver, settings, batch_id=UUID(args.batch_id), presets=presets
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_extract_surface(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    presets = tuple(args.preset) if args.preset else (S9_DEFAULT_PRESET,)
    with get_pipeline_session(settings) as session:
        result = extract_surface_batch(
            session, silver, settings, batch_id=UUID(args.batch_id), presets=presets
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_postprocess_mesh(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    presets = tuple(args.preset) if args.preset else (S10_DEFAULT_PRESET,)
    target_faces = args.target_faces if args.target_faces else DEFAULT_TARGET_FACES
    with get_pipeline_session(settings) as session:
        result = postprocess_mesh_batch(
            session,
            silver,
            settings,
            batch_id=UUID(args.batch_id),
            presets=presets,
            target_faces=target_faces,
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    settings = get_settings()
    silver = silver_client(settings)
    gold = gold_client(settings)
    preset = args.preset if args.preset else S11_DEFAULT_PRESET
    with get_pipeline_session(settings) as session:
        result = export_batch(
            session, silver, gold, settings, batch_id=UUID(args.batch_id), preset=preset
        )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def _cmd_publish_contracts(args: argparse.Namespace) -> int:
    with get_pipeline_session(get_settings()) as session:
        published = publish_all(session)
    print(json.dumps({"newly_published": published}))
    return 0


def _cmd_backfill_lineage(args: argparse.Namespace) -> int:
    with get_pipeline_session(get_settings()) as session:
        added = backfill_edges(session)
    print(json.dumps({"edges_added": added}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_parser = sub.add_parser("ingest", help="S1 -- ingest an archive or directory")
    ingest_parser.add_argument("path")
    ingest_parser.add_argument("--source", required=True)
    ingest_parser.set_defaults(func=_cmd_ingest)

    parse_index_parser = sub.add_parser("parse-index", help="S2 -- parse & index a batch")
    parse_index_parser.add_argument("batch_id")
    parse_index_parser.set_defaults(func=_cmd_parse_index)

    quality_gate_parser = sub.add_parser("quality-gate", help="S3 -- quality gate a batch's series")
    quality_gate_parser.add_argument("batch_id")
    quality_gate_parser.set_defaults(func=_cmd_quality_gate)

    deidentify_parser = sub.add_parser(
        "deidentify", help="S4/S5 -- de-identify and verify a batch's series"
    )
    deidentify_parser.add_argument("batch_id")
    deidentify_parser.set_defaults(func=_cmd_deidentify)

    assemble_volume_parser = sub.add_parser(
        "assemble-volume", help="S6 -- assemble a batch's series into NIfTI volumes"
    )
    assemble_volume_parser.add_argument("batch_id")
    assemble_volume_parser.set_defaults(func=_cmd_assemble_volume)

    deface_parser = sub.add_parser(
        "deface", help="S7 -- decide and apply defacing for a batch's series volumes"
    )
    deface_parser.add_argument("batch_id")
    deface_parser.set_defaults(func=_cmd_deface)

    segment_parser = sub.add_parser(
        "segment", help="S8 -- threshold + clean a batch's gated volumes into masks"
    )
    segment_parser.add_argument("batch_id")
    segment_parser.add_argument("--preset", action="append", help="repeatable; defaults to bone")
    segment_parser.set_defaults(func=_cmd_segment)

    extract_surface_parser = sub.add_parser(
        "extract-surface", help="S9 -- marching cubes from a batch's gated volumes + S8 masks"
    )
    extract_surface_parser.add_argument("batch_id")
    extract_surface_parser.add_argument(
        "--preset", action="append", help="repeatable; defaults to bone"
    )
    extract_surface_parser.set_defaults(func=_cmd_extract_surface)

    postprocess_mesh_parser = sub.add_parser(
        "postprocess-mesh", help="S10 -- largest component, smoothing, decimation, normals"
    )
    postprocess_mesh_parser.add_argument("batch_id")
    postprocess_mesh_parser.add_argument(
        "--preset", action="append", help="repeatable; defaults to bone"
    )
    postprocess_mesh_parser.add_argument(
        "--target-faces", type=int, default=None, help="defaults to 200000"
    )
    postprocess_mesh_parser.set_defaults(func=_cmd_postprocess_mesh)

    export_parser = sub.add_parser(
        "export", help="S11 -- GLB/volume/thumbnail/manifest into gold, one preset per run"
    )
    export_parser.add_argument("batch_id")
    export_parser.add_argument("--preset", default=None, help="defaults to bone")
    export_parser.set_defaults(func=_cmd_export)

    publish_parser = sub.add_parser(
        "publish-contracts", help="publish the data contracts to public.data_contracts"
    )
    publish_parser.set_defaults(func=_cmd_publish_contracts)

    backfill_parser = sub.add_parser(
        "backfill-lineage", help="derive lineage_edges for every successful run (idempotent)"
    )
    backfill_parser.set_defaults(func=_cmd_backfill_lineage)

    return parser


def app() -> None:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    app()
