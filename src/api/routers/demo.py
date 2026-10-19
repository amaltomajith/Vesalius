"""The point-and-click demo interface's server side.

Unlike `catalog.py` (read-only, `serving_ro`, gold only) this router STARTS pipeline work, so it
is a separate module with its own trust boundary. It is meant for a single local user: there is
no login, and the compose file binds the port to 127.0.0.1. Do not expose it to a network as it is.

What it never does: read bronze, silver or restricted data. Results come from the S12 queries
(gold-only, serving role) and from gold objects looked up through them.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api import demo_state
from api.auth import proof_limit, require_admin, run_limit
from api.dependencies import serving_session_dependency, settings_dependency
from pipeline.common.config import Settings
from pipeline.demo import datasets, declaration, proofs
from pipeline.demo.report import build_report_html, render_preview
from pipeline.demo.runner import BusyError
from pipeline.stages.s12_serve import queries
from pipeline.storage.object_store import get_bytes, gold_client

router = APIRouter(prefix="/demo")

MAX_UPLOAD_MB = 200
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
_CHUNK = 1024 * 1024
BUSY_MESSAGE = "Another job is still running. Please wait until it finishes, then try again."

ServingSession = Annotated[Session, Depends(serving_session_dependency)]


class SampleRequest(BaseModel):
    dataset: str


def _busy() -> HTTPException:
    return HTTPException(status_code=409, detail=BUSY_MESSAGE)


@router.get("/datasets")
def list_datasets() -> dict[str, Any]:
    return {
        "datasets": datasets.catalogue(),
        "proofs": proofs.catalogue(),
        "upload": {
            "max_megabytes": MAX_UPLOAD_MB,
            "presets": list(datasets.UPLOAD_PRESETS),
        },
        "busy": demo_state.RUNNER.busy,
        "latest_run": demo_state.RUNNER.latest_id(),
    }


@router.post("/runs/sample", dependencies=[Depends(run_limit)])  # synthetic/public samples
def start_sample(request: SampleRequest) -> dict[str, str]:
    if request.dataset not in datasets.DATASETS:
        raise HTTPException(status_code=400, detail="Unknown sample scan.")
    if demo_state.RUNNER.busy:
        raise _busy()
    built = datasets.build(request.dataset)
    tmp = tempfile.mkdtemp(prefix="demo_")
    zip_path = str(Path(tmp) / f"{request.dataset}.zip")
    Path(zip_path).write_bytes(built.zip_bytes)
    try:
        run = demo_state.RUNNER.start(
            dataset=request.dataset,
            title=built.info.title,
            preset=built.info.preset,
            zip_path=zip_path,
            planted=[asdict(p) for p in built.planted],
            cleanup_dir=tmp,
        )
    except BusyError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise _busy() from exc
    return {"run_id": run.id}


@router.post("/runs/upload", dependencies=[Depends(require_admin)])
async def start_upload(
    file: Annotated[UploadFile, File()],
    acknowledged: Annotated[bool, Form()] = False,
    preset: Annotated[str, Form()] = "bone",
    body_part: Annotated[str, Form()] = "auto",
) -> dict[str, str]:
    if not acknowledged:
        raise HTTPException(
            status_code=400,
            detail="Please confirm the data is public or anonymous. Real patient data must "
            "never be uploaded here.",
        )
    if preset not in datasets.UPLOAD_PRESETS:
        raise HTTPException(
            status_code=400, detail="Choose bone, skin surface or named structures."
        )
    if not declaration.is_valid(body_part):
        raise HTTPException(
            status_code=400, detail="Choose head, chest, abdomen, or let the files say."
        )
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Please upload a .zip file of DICOM images.")
    if demo_state.RUNNER.busy:
        raise _busy()

    tmp = tempfile.mkdtemp(prefix="demo_")
    zip_path = str(Path(tmp) / "upload.zip")
    size = 0
    with open(zip_path, "wb") as out:
        while chunk := await file.read(_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                out.close()
                shutil.rmtree(tmp, ignore_errors=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"That file is too large (limit {MAX_UPLOAD_MB} MB).",
                )
            out.write(chunk)
    if not zipfile.is_zipfile(zip_path):
        shutil.rmtree(tmp, ignore_errors=True)
        raise HTTPException(status_code=400, detail="That file is not a valid zip archive.")
    try:
        run = demo_state.RUNNER.start(
            dataset=datasets.UPLOAD_KEY,
            title="Your uploaded scan",  # never the file name: it can itself contain identifiers
            preset=preset,
            zip_path=zip_path,
            cleanup_dir=tmp,
            body_part=body_part,
        )
    except BusyError as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise _busy() from exc
    return {"run_id": run.id}


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = demo_state.RUNNER.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such run. It may have been cleared.")
    return run


# ---- proofs ------------------------------------------------------------------------------------


@router.post("/proofs/{key}", dependencies=[Depends(proof_limit)])
def start_proof(key: str) -> dict[str, str]:
    if key not in proofs.PROOFS:
        raise HTTPException(status_code=404, detail="Unknown proof.")
    try:
        job = demo_state.PROOF_RUNNER.start(key)
    except BusyError as exc:
        raise _busy() from exc
    return {"job_id": job.id}


@router.get("/proofs/last")
def last_proofs() -> dict[str, Any]:
    return dict(demo_state.LAST_PROOF)


@router.get("/proofs/image", include_in_schema=False)
def proof_image() -> FileResponse:
    if not proofs.PROOF_IMAGE.exists():
        raise HTTPException(status_code=404, detail="Run the face-removal proof first.")
    return FileResponse(proofs.PROOF_IMAGE, media_type="image/png")


@router.get("/proofs/jobs/{job_id}")
def get_proof_job(job_id: str) -> dict[str, Any]:
    job = demo_state.PROOF_RUNNER.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such proof run.")
    if job["state"] == "finished":
        demo_state.LAST_PROOF[job["key"]] = job
    return job


# ---- results: gold files and the shareable report -----------------------------------------------

_MEDIA = {"glb": "model/gltf-binary", "png": "image/png", "json": "application/json"}


@router.get("/files/{artifact_id}")
def get_file(
    artifact_id: str,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
    download: bool = False,
) -> Response:
    """Streams one GOLD artifact through the app (so the 3D viewer needs no cross-origin call to
    the object store). The lookup is the S12 query, which only ever returns gold rows."""
    from uuid import UUID

    try:
        aid = UUID(artifact_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="File not found.") from exc
    found = queries.get_gold_artifact(session, aid)
    if found is None:
        raise HTTPException(status_code=404, detail="File not found.")
    _, key = found
    data = get_bytes(gold_client(settings), settings.minio_gold_bucket, key)
    name = key.rsplit("/", 1)[-1]
    media = _MEDIA.get(name.rsplit(".", 1)[-1], "application/octet-stream")
    headers = {"Content-Disposition": f'attachment; filename="{name}"'} if download else {}
    return Response(content=data, media_type=media, headers=headers)


@router.get("/report/{run_id}", response_class=HTMLResponse)
def get_report(
    run_id: str,
    session: ServingSession,
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> HTMLResponse:
    run = demo_state.RUNNER.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No such run. It may have been cleared.")

    series = quality = None
    thumbnail = preview = None
    if run["series_ids"]:
        from uuid import UUID

        sid = UUID(run["series_ids"][0])
        try:
            version = queries.current_contract_version(session)
        except queries.NoContractPublishedError:
            version = None
        if version:
            series = queries.get_series(session, sid, version)
            quality = queries.quality_report(session, sid, version)
            gold = gold_client(settings)
            for art in queries.gold_artifacts(session, sid):
                got = queries.get_gold_artifact(session, art.id)
                if got is None:
                    continue
                if art.kind == "thumbnail":
                    thumbnail = get_bytes(gold, settings.minio_gold_bucket, got[1])
                elif art.kind == "surface_glb":
                    preview = render_preview(get_bytes(gold, settings.minio_gold_bucket, got[1]))
    html = build_report_html(
        {
            "run": run,
            "series": series.model_dump(mode="json") if series else None,
            "quality": quality.model_dump(mode="json") if quality else None,
            "thumbnail_png": thumbnail,
            "preview_png": preview,
            "proofs": [
                {**job, "title": proofs.PROOFS[key].title}
                for key, job in demo_state.LAST_PROOF.items()
            ],
        }
    )
    return HTMLResponse(html)
