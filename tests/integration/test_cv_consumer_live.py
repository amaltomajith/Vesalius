"""The CV consumer against the REAL system: a uvicorn subprocess serving the S12 API over live
Postgres/MinIO, presigned downloads over HTTP, and the outputs of the real S1->S11 chain.

The point: prove gold is a usable input to an independent consumer, and that the consumer refuses
data that is intact but wrong (matching hash, mirrored geometry) as well as data that is corrupt.
"""

from __future__ import annotations

import gzip
import json
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator

import pytest

from consumer.client import Client, http_fetch, run
from consumer.contract import ContractViolation, IntegrityError
from tests.fixtures.shared import integration, require_integration

# module-scoped data builders shared with the API tests (they build once per module)
from tests.integration.test_s12_api import (  # noqa: F401
    ServedChest,
    contracts_published,
    served_chest,
    served_head,
)


@pytest.fixture(scope="module")
def base_url(contracts_published) -> Iterator[str]:  # type: ignore[no-untyped-def]  # noqa: F811
    require_integration()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(url + "/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("uvicorn did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=15)


def _rewrite(base: str, transform):  # type: ignore[no-untyped-def]
    """A fetch that passes everything through but lets a test alter selected responses."""

    def fetch(url: str) -> bytes:
        return transform(url, http_fetch(url))  # type: ignore[no-any-return]

    return Client(base, fetch=fetch)


@integration
def test_the_consumer_runs_on_a_defaced_head_and_the_nose_is_gone(
    base_url: str,
    served_head,  # noqa: F811
) -> None:
    out = run(Client(base_url), str(served_head.series_id))
    f = out["features"]
    assert out["contract_version"] == "1.0"
    assert f["body_volume_ml"] > 1000 and f["class_volume_ml"]["bone"] > 0
    # the raw phantom's nose reaches y = +123 mm (anterior); the served, defaced volume must not
    assert f["extent_ras_mm"]["max"][1] < 118.0


@integration
def test_the_consumer_runs_on_a_real_chest_chain(base_url: str, served_chest) -> None:  # noqa: F811
    out = run(Client(base_url), str(served_chest.served.series_id))
    assert out["contract_version"] == "1.0"
    assert out["features"]["voxel_volume_ml"] > 0
    assert json.dumps(out, sort_keys=True) == json.dumps(
        run(Client(base_url), str(served_chest.served.series_id)), sort_keys=True
    )  # deterministic


@integration
def test_the_cli_prints_the_feature_table_and_exits_zero(base_url: str, served_head) -> None:  # noqa: F811
    r = subprocess.run(
        [sys.executable, "-m", "consumer", base_url, str(served_head.series_id)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["features"]["body_volume_ml"] > 0


@integration
def test_the_cli_refuses_an_unknown_series_with_a_clear_error(base_url: str) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "consumer", base_url, "00000000-0000-0000-0000-000000000000"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert r.returncode == 1 and "HTTP 404" in r.stderr


@integration
def test_a_corrupted_download_is_an_integrity_error(base_url: str, served_head) -> None:  # noqa: F811
    def flip(url: str, data: bytes) -> bytes:
        if "/gold/" in url and "volume" in url:
            return data[:-1] + bytes([data[-1] ^ 0xFF])
        return data

    with pytest.raises(IntegrityError):
        run(_rewrite(base_url, flip), str(served_head.series_id))


@integration
def test_an_lps_contract_from_the_api_is_refused_end_to_end(base_url: str, served_head) -> None:  # noqa: F811
    def to_lps(url: str, data: bytes) -> bytes:
        if "/contracts/volume_contract/" in url:
            body = json.loads(data)
            body["schema"]["spatial"]["coordinate_frame"] = "LPS"
            body["schema"]["version"] = "1.1"
            return json.dumps(body).encode()
        return data

    with pytest.raises(ContractViolation, match="RAS"):
        run(_rewrite(base_url, to_lps), str(served_head.series_id))


@integration
def test_intact_bytes_with_mirrored_geometry_are_still_refused(base_url: str, served_head) -> None:  # noqa: F811
    """The hard case: hash and size MATCH the (rewritten) catalog, so integrity passes -- only
    the contract's geometry check can stop a volume whose world x-axis is flipped."""

    def mirror(raw: bytes) -> bytes:
        buf = bytearray(gzip.decompress(raw))
        for off in (280, 296, 312):
            (v,) = struct.unpack_from("<f", buf, off)
            struct.pack_into("<f", buf, off, -v)
        return gzip.compress(bytes(buf), mtime=0)

    state: dict[str, bytes] = {}

    def transform(url: str, data: bytes) -> bytes:
        if url.startswith(base_url + "/artifacts/"):
            body = json.loads(data)
            if body["artifact"]["kind"] == "volume_nifti":
                from urllib.request import urlopen

                mirrored = mirror(urlopen(body["url"]).read())
                state["volume"] = mirrored
                import hashlib

                body["artifact"]["sha256"] = hashlib.sha256(mirrored).hexdigest()
                body["artifact"]["bytes"] = len(mirrored)
                body["url"] = "mirror://volume"
            return json.dumps(body).encode()
        if url == "mirror://volume":
            return state["volume"]
        return data

    def fetch(url: str) -> bytes:
        if url == "mirror://volume":
            return state["volume"]
        return transform(url, http_fetch(url))

    with pytest.raises(ContractViolation, match="mirrored"):
        run(Client(base_url, fetch=fetch), str(served_head.series_id))
