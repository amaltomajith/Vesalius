"""The consumer's client (`run`) against a fake API, plus the structural guard that keeps the
consumer independent of the pipeline it is supposed to be able to refuse."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from consumer import __main__ as cli
from consumer.client import Client, ConsumerError, run
from consumer.contract import ContractViolation, IntegrityError
from pipeline.catalog.contracts import VOLUME_CONTRACT_V1_0
from tests.fixtures.deface_runs import nifti_bytes

ROOT = Path(__file__).parents[2] / "src" / "consumer"
BASE = "http://api.test"
SID = "11111111-1111-1111-1111-111111111111"
ARRAY = (np.arange(4 * 5 * 6, dtype=np.float32).reshape(4, 5, 6) * 3.0 - 200.0).astype(np.float32)
VOLUME = nifti_bytes(ARRAY, np.eye(4))
MANIFEST = json.dumps(
    {
        "contract_version": "1.0",
        "geometry": {"source_frame": "RAS_mm"},
        "deface": {"required": True, "applied": True},
    }
).encode()


class FakeApi:
    def __init__(self) -> None:
        self.contract = copy.deepcopy(VOLUME_CONTRACT_V1_0)
        self.volume, self.manifest = VOLUME, MANIFEST
        self.volume_contract_version: str | None = "1.0"
        self.kinds = ("volume_nifti", "manifest")
        self.claimed_sha: str | None = None
        self.claimed_bytes: int | None = None
        self.fail: str | None = None
        self.contract_requests: list[str] = []

    def _meta(self, aid: str, data: bytes, kind: str) -> dict:  # type: ignore[type-arg]
        return {
            "id": aid,
            "series_id": SID,
            "kind": kind,
            "format": "x",
            "sha256": self.claimed_sha or hashlib.sha256(data).hexdigest(),
            "bytes": self.claimed_bytes if self.claimed_bytes is not None else len(data),
            "contract_version": self.volume_contract_version if kind == "volume_nifti" else None,
            "created_at": "2026-01-01T00:00:00Z",
        }

    def __call__(self, url: str) -> bytes:
        if self.fail and self.fail in url:
            raise ConsumerError(f"GET {url} -> HTTP 500")
        path = url.removeprefix(BASE)
        if path == f"/series/{SID}/artifacts":
            arts = [self._meta(f"id-{k}", b"", k) for k in self.kinds]
            return json.dumps({"artifacts": arts}).encode()
        if path.startswith("/artifacts/id-"):
            kind = path.removeprefix("/artifacts/id-")
            data = self.volume if kind == "volume_nifti" else self.manifest
            meta = self._meta(f"id-{kind}", data, kind)
            return json.dumps({"artifact": meta, "url": f"http://minio.test/{kind}"}).encode()
        if path.startswith("/contracts/volume_contract/"):
            self.contract_requests.append(path.rsplit("/", 1)[1])
            return json.dumps({"schema": self.contract}).encode()
        if url == "http://minio.test/volume_nifti":
            return self.volume
        if url == "http://minio.test/manifest":
            return self.manifest
        raise AssertionError(f"unexpected URL {url}")


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


def _run(api: FakeApi) -> dict:  # type: ignore[type-arg]
    return run(Client(BASE, fetch=api), SID)


def test_a_valid_series_yields_a_feature_table_under_its_contract_version(api: FakeApi) -> None:
    out = _run(api)
    assert out["series_id"] == SID and out["contract_version"] == "1.0"
    assert out["features"]["shape_zyx"] == [4, 5, 6]


def test_it_validates_against_the_contract_version_the_artifact_declares(api: FakeApi) -> None:
    api.volume_contract_version = "1.3"
    api.manifest = json.dumps(
        {
            "contract_version": "1.3",
            "geometry": {"source_frame": "RAS_mm"},
            "deface": {"required": False, "applied": False},
        }
    ).encode()
    api.contract["version"] = "1.3"
    out = _run(api)
    assert api.contract_requests == ["1.3"] and out["contract_version"] == "1.3"


def test_bytes_that_differ_from_the_catalog_hash_are_refused(api: FakeApi) -> None:
    api.claimed_sha = "0" * 64
    with pytest.raises(IntegrityError, match="does not match the catalog"):
        _run(api)


def test_a_catalog_size_that_disagrees_with_a_hash_matching_download_is_refused(
    api: FakeApi,
) -> None:
    """The hash matches, so only the size check can catch an internally inconsistent catalog."""
    api.claimed_bytes = len(VOLUME) + 7
    with pytest.raises(IntegrityError, match="does not match the catalog"):
        _run(api)


def test_a_download_of_the_wrong_size_is_refused(api: FakeApi) -> None:
    real = api.__call__

    def short(url: str) -> bytes:
        data = real(url)
        return data[:-1] if url == "http://minio.test/volume_nifti" else data

    with pytest.raises(IntegrityError):
        run(Client(BASE, fetch=short), SID)


def test_a_series_missing_its_volume_or_manifest_is_refused(api: FakeApi) -> None:
    for kinds in (("manifest",), ("volume_nifti",)):
        api.kinds = kinds
        with pytest.raises(ContractViolation, match="no .* artifact"):
            _run(api)


def test_a_volume_declaring_no_contract_version_is_refused(api: FakeApi) -> None:
    api.volume_contract_version = None
    with pytest.raises(ContractViolation, match="no contract version"):
        _run(api)


def test_a_contract_the_api_serves_as_an_unsupported_major_is_refused(api: FakeApi) -> None:
    api.contract["version"] = "2.0"
    with pytest.raises(ContractViolation, match="major version"):
        _run(api)


def test_an_lps_contract_from_the_api_is_refused(api: FakeApi) -> None:
    api.contract["spatial"]["coordinate_frame"] = "LPS"
    with pytest.raises(ContractViolation, match="RAS"):
        _run(api)


def test_a_head_volume_whose_manifest_says_defacing_was_skipped_is_refused(api: FakeApi) -> None:
    api.manifest = json.dumps(
        {
            "contract_version": "1.0",
            "geometry": {"source_frame": "RAS_mm"},
            "deface": {"required": True, "applied": False},
        }
    ).encode()
    with pytest.raises(ContractViolation, match="defacing"):
        _run(api)


def test_an_api_error_surfaces_as_a_consumer_error(api: FakeApi) -> None:
    api.fail = "/artifacts/"
    with pytest.raises(ConsumerError, match="HTTP 500"):
        _run(api)


@pytest.mark.parametrize(
    "exc, code",
    [
        (ContractViolation("expected RAS"), 3),
        (IntegrityError("hash"), 4),
        (ConsumerError("down"), 1),
    ],
)
def test_the_cli_maps_each_refusal_to_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exc: Exception, code: int
) -> None:
    def boom(*_a: object, **_k: object) -> dict:  # type: ignore[type-arg]
        raise exc

    monkeypatch.setattr(cli, "run", boom)
    assert cli.main(["http://x", SID]) == code
    assert capsys.readouterr().err.strip()


def test_the_cli_prints_sorted_json_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "run", lambda *_a, **_k: {"b": 1, "a": 2})
    assert cli.main(["http://x", SID]) == 0
    assert list(json.loads(capsys.readouterr().out)) == ["a", "b"]
    assert cli.main([]) == 2


# ---- independence -------------------------------------------------------------------------------

ALLOWED_THIRD_PARTY = {"numpy"}


def _top_level_imports(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_the_consumer_imports_only_numpy_the_stdlib_and_itself() -> None:
    files = sorted(ROOT.glob("*.py"))
    assert len(files) >= 6
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | {"consumer"}
    for f in files:
        extra = _top_level_imports(f.read_text(encoding="utf-8")) - allowed
        assert extra == set(), f"{f.name} imports {sorted(extra)}"


@pytest.mark.parametrize(
    "source, extra",
    [
        ("from pipeline.catalog import models", {"pipeline"}),
        ("import sqlalchemy", {"sqlalchemy"}),
        ("import minio.api", {"minio"}),
        ("import SimpleITK as sitk", {"SimpleITK"}),
        ("import json, numpy, consumer.loader", set()),
    ],
)
def test_the_independence_scanner_can_fail(source: str, extra: set[str]) -> None:
    allowed = set(sys.stdlib_module_names) | ALLOWED_THIRD_PARTY | {"consumer"}
    assert _top_level_imports(source) - allowed == extra
