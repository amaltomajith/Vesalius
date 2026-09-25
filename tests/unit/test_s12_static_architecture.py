"""Structural proof that S12 can only ever read the catalog through the serving role and objects
from gold. Scans the S12 package and the serving router for anything that would reach silver,
bronze, quarantine or the write-role session. Limits: an AST scan of this repository -- it stops
accidents, not a determined edit.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2] / "src"
S12_FILES = sorted((ROOT / "pipeline" / "stages" / "s12_serve").rglob("*.py")) + [
    ROOT / "api" / "routers" / "catalog.py"
]

FORBIDDEN_NAMES = {
    "silver_client",
    "bronze_client",
    "quarantine_client",
    "get_pipeline_session",
    "pipeline_session_dependency",
    "bronze_client_dependency",
    "minio_silver_bucket",
    "minio_bronze_bucket",
    "minio_quarantine_bucket",
    "pipeline_database_url",
}
# the only object-store constructors S12 may touch
ALLOWED_STORE_NAMES = {"gold_presign_client"}


def _names(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
    return found


def _store_imports(source: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module == "pipeline.storage.object_store":
            out.update(a.name for a in node.names)
    return out


def test_the_files_this_guard_scans_exist() -> None:
    assert len(S12_FILES) >= 6
    assert all(p.is_file() for p in S12_FILES)


@pytest.mark.parametrize("path", S12_FILES, ids=lambda p: p.name)
def test_s12_never_reaches_silver_bronze_quarantine_or_the_write_session(path: Path) -> None:
    hits = _names(path.read_text(encoding="utf-8")) & FORBIDDEN_NAMES
    assert hits == set(), f"{path.name} references {sorted(hits)}"


@pytest.mark.parametrize("path", S12_FILES, ids=lambda p: p.name)
def test_s12_only_imports_the_gold_presign_helper_from_the_object_store(path: Path) -> None:
    extra = _store_imports(path.read_text(encoding="utf-8")) - ALLOWED_STORE_NAMES
    assert extra == set(), f"{path.name} imports {sorted(extra)} from object_store"


def test_the_presign_function_has_no_bucket_parameter() -> None:
    """Gold-only by construction: a caller cannot ask it to sign another bucket."""
    import inspect

    from pipeline.stages.s12_serve.presign import presign_gold_url

    assert list(inspect.signature(presign_gold_url).parameters) == ["settings", "object_key"]


def test_every_artifact_query_filters_on_the_served_zone() -> None:
    """Each `select(Artifact...)` in queries.py must mention `SERVED_ZONE` in the same call."""
    src = (ROOT / "pipeline" / "stages" / "s12_serve" / "queries.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        body = ast.get_source_segment(src, fn) or ""
        if "select(Artifact" in body:
            assert "SERVED_ZONE" in body, f"{fn.name} selects artifacts without a zone filter"


@pytest.mark.parametrize(
    "source, hit",
    [
        ("from pipeline.storage.object_store import silver_client", "silver_client"),
        ("x = settings.minio_silver_bucket", "minio_silver_bucket"),
        ("from pipeline.catalog.session import get_pipeline_session", "get_pipeline_session"),
        ("x = 1", None),
    ],
)
def test_the_name_scanner_can_fail(source: str, hit: str | None) -> None:
    assert (_names(source) & FORBIDDEN_NAMES) == ({hit} if hit else set())


def test_the_store_import_scanner_can_fail() -> None:
    assert _store_imports("from pipeline.storage.object_store import put_bytes") == {"put_bytes"}
    assert _store_imports("from other import put_bytes") == set()
