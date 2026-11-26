"""A person-stated body part for uploaded scans, against the REAL system (live Postgres + MinIO),
driven through the same upload route the page uses.

The scans are the demo phantoms with their body-part label removed and every UID regenerated, so
each test is a brand-new series (nothing is deduplicated against an earlier run).
OCR is stubbed as in `test_demo_live.py`; everything else is real.
"""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Iterator
from unittest import mock

import pydicom
import pytest
from fastapi.testclient import TestClient
from pydicom.uid import generate_uid

from api import demo_state
from api.main import create_app
from pipeline.demo import datasets
from pipeline.demo.dicom_series import build_demo_series
from pipeline.demo.runner import PipelineRunner, real_deps
from tests.fixtures.shared import integration, require_integration


@pytest.fixture(scope="module", autouse=True)
def no_ocr() -> Iterator[None]:
    require_integration()
    with (
        mock.patch(
            "pipeline.stages.s4_deidentify.pixel_redaction.detect_text_regions", lambda i: []
        ),
        mock.patch("pipeline.stages.s5_verify.pixel_verify.detect_text_regions", lambda i: []),
    ):
        yield


@pytest.fixture(scope="module")
def client() -> TestClient:
    require_integration()
    lock = threading.Lock()
    demo_state.HEAVY_LOCK = lock
    demo_state.RUNNER = PipelineRunner(real_deps, lock=lock)
    return TestClient(create_app())


def _scan_zip(kind: str, *, label: bool, description: str) -> bytes:
    """A demo scan, made brand new (fresh UIDs), with the body-part tag optionally removed and the
    study description replaced."""
    built = build_demo_series(kind, plant_identifiers=False)
    study_uid, series_uid = generate_uid(), generate_uid()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, blob in built.files.items():
            ds = pydicom.dcmread(io.BytesIO(blob))
            ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
            ds.SOPInstanceUID = generate_uid()
            ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
            ds.StudyDescription = description
            if not label and "BodyPartExamined" in ds:
                del ds.BodyPartExamined
            out = io.BytesIO()
            ds.save_as(out)
            zf.writestr(name, out.getvalue())
    return buf.getvalue()


def _upload(client: TestClient, blob: bytes, body_part: str, preset: str) -> dict:  # type: ignore[type-arg]
    r = client.post(
        "/demo/runs/upload",
        files={"file": ("scan.zip", blob, "application/zip")},
        data={"acknowledged": "true", "preset": preset, "body_part": body_part},
    )
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    for _ in range(1200):
        run = client.get(f"/demo/runs/{run_id}").json()
        if run["state"] == "finished":
            return run  # type: ignore[no-any-return]
        threading.Event().wait(0.25)
    raise AssertionError("the run did not finish in 5 minutes")


def _series(client: TestClient, run: dict) -> dict:  # type: ignore[type-arg]
    assert run["outcome"] == "success", run["message"]
    return client.get(f"/series/{run['series_ids'][0]}").json()  # type: ignore[no-any-return]


@integration
def test_an_unlabelled_scan_is_still_refused_when_nothing_is_stated(client: TestClient) -> None:
    run = _upload(client, _scan_zip("chest", label=False, description="UNKNOWN"), "auto", "bone")
    assert run["outcome"] == "rejected"
    assert "does not clearly say which part of the body" in run["message"]


@integration
def test_a_stated_chest_is_accepted_recorded_and_needs_no_face_removal(client: TestClient) -> None:
    run = _upload(client, _scan_zip("chest", label=False, description="UNKNOWN"), "chest", "bone")
    series = _series(client, run)
    assert series["body_part"] == "CHEST" and series["deface_required"] is False
    quality = next(s for s in run["steps"] if s["key"] == "quality")
    assert "your statement (a chest scan) was used" in quality["detail"]


@integration
def test_a_stated_head_is_accepted_and_the_face_is_removed(client: TestClient) -> None:
    run = _upload(
        client, _scan_zip("head", label=False, description="UNKNOWN"), "head", "skin_surface"
    )
    series = _series(client, run)
    assert series["body_part"] == "HEAD" and series["deface_required"] is True
    face = next(s for s in run["steps"] if s["key"] == "face")
    assert "Face removed" in face["detail"]


@integration
def test_a_person_cannot_talk_the_pipeline_out_of_defacing_a_described_head(
    client: TestClient,
) -> None:
    """The files say 'CT HEAD' in their description and carry no label. Stating 'chest' must not
    win: the statement is refused, S3 infers the head itself, and the face is removed."""
    run = _upload(
        client, _scan_zip("head", label=False, description="CT HEAD"), "chest", "skin_surface"
    )
    series = _series(client, run)
    assert series["deface_required"] is True and series["body_part"] == "HEAD"
    quality = next(s for s in run["steps"] if s["key"] == "quality")
    assert "the files describe a head" in quality["detail"]
    face = next(s for s in run["steps"] if s["key"] == "face")
    assert "Face removed" in face["detail"]


@integration
def test_a_label_carried_by_the_files_always_wins(client: TestClient) -> None:
    run = _upload(client, _scan_zip("chest", label=True, description="CT CHEST"), "head", "bone")
    series = _series(client, run)
    assert series["body_part"] == "CHEST" and series["deface_required"] is False
    quality = next(s for s in run["steps"] if s["key"] == "quality")
    assert "carry their own body-part label" in quality["detail"]


@integration
def test_an_invalid_statement_is_a_plain_400(client: TestClient) -> None:
    blob = datasets.build("chest").zip_bytes
    r = client.post(
        "/demo/runs/upload",
        files={"file": ("scan.zip", blob, "application/zip")},
        data={"acknowledged": "true", "preset": "bone", "body_part": "wholebody"},
    )
    assert r.status_code == 400 and "head, chest, abdomen" in r.json()["detail"]


@integration
def test_the_same_refused_scan_can_be_uploaded_again_with_its_body_part_stated(
    client: TestClient,
) -> None:
    """The real flow: upload, get refused as unlabelled, upload the SAME zip again saying what it
    is. The second attempt must be evaluated afresh, not answered from the first refusal."""
    blob = _scan_zip("chest", label=False, description="UNKNOWN")
    first = _upload(client, blob, "auto", "bone")
    assert first["outcome"] == "rejected"
    second = _upload(client, blob, "chest", "bone")
    series = _series(client, second)
    assert series["body_part"] == "CHEST" and series["deface_required"] is False
