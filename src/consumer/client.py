"""HTTP client for the S12 API plus the end-to-end `run`. Standard library only.

The consumer validates against the contract version the ARTIFACT says it was produced under
(fetched from the API), verifies downloaded bytes against the catalog's sha256, and only then
loads and computes features.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from consumer.contract import SUPPORTED_ARTIFACT_KIND, ContractViolation, IntegrityError
from consumer.features import compute_features
from consumer.loader import load

CONTRACT_NAME = "volume_contract"
MANIFEST_KIND = "manifest"

Fetch = Callable[[str], bytes]


class ConsumerError(Exception):
    """The API could not be reached or answered with an error."""


def http_fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 -- URL is the caller's
            data: bytes = resp.read()
            return data
    except urllib.error.HTTPError as exc:
        raise ConsumerError(f"GET {url} -> HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ConsumerError(f"GET {url} failed: {exc.reason}") from exc


class Client:
    def __init__(self, base_url: str, fetch: Fetch = http_fetch) -> None:
        self.base = base_url.rstrip("/")
        self._fetch = fetch

    def get_json(self, path: str) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self._fetch(self.base + path))
        return body

    def download(self, url: str) -> bytes:
        return self._fetch(url)


def _download_verified(client: Client, artifact_id: str) -> tuple[dict[str, Any], bytes]:
    info = client.get_json(f"/artifacts/{artifact_id}")
    meta = info["artifact"]
    data = client.download(info["url"])
    if len(data) != meta["bytes"] or hashlib.sha256(data).hexdigest() != meta["sha256"]:
        raise IntegrityError(
            f"downloaded {meta['kind']} ({len(data)} bytes) does not match the catalog "
            f"({meta['bytes']} bytes, sha256 {meta['sha256'][:12]}...)"
        )
    return meta, data


def run(client: Client, series_id: str) -> dict[str, Any]:
    listing = client.get_json(f"/series/{series_id}/artifacts")
    by_kind = {a["kind"]: a for a in listing["artifacts"]}
    for kind in (SUPPORTED_ARTIFACT_KIND, MANIFEST_KIND):
        if kind not in by_kind:
            raise ContractViolation(f"series has no {kind} artifact in gold")

    volume_meta, volume_bytes = _download_verified(client, by_kind[SUPPORTED_ARTIFACT_KIND]["id"])
    _, manifest_bytes = _download_verified(client, by_kind[MANIFEST_KIND]["id"])

    version = volume_meta.get("contract_version")
    if not version:
        raise ContractViolation("volume artifact declares no contract version")
    contract = client.get_json(f"/contracts/{CONTRACT_NAME}/{version}")["schema"]

    volume = load(volume_bytes, contract, json.loads(manifest_bytes))
    return {
        "series_id": series_id,
        "contract_version": version,
        "features": compute_features(volume),
    }
