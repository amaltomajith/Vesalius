"""Structural guarantees of the demo interface, checked by scanning the repository.

* production code never imports test fixtures;
* the demo router (which starts pipeline work) cannot reach bronze/silver directly;
* the page cannot inject markup or make an external request (it must work offline);
* every network-facing port in docker-compose.yml is bound to loopback (there is no login).
Limits: textual scans of this repository. They stop accidents, not a determined edit.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SRC = ROOT / "src"
STATIC = SRC / "api" / "static"
SLICER_CT_SHA256 = "6a5b6caccb76576a863beb095e3bfb910c50ca78f4c9bf043aa42f976cfa53d1"


def _py(*roots: Path) -> list[Path]:
    return sorted(p for r in roots for p in r.rglob("*.py"))


def _imported(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
    return found


@pytest.mark.parametrize("path", _py(SRC / "pipeline" / "demo", SRC / "api"), ids=lambda p: p.name)
def test_production_code_never_imports_test_fixtures(path: Path) -> None:
    assert "tests" not in _imported(path.read_text(encoding="utf-8"))


FORBIDDEN_IN_DEMO_ROUTER = {
    "silver_client",
    "bronze_client",
    "get_pipeline_session",
    "pipeline_session_dependency",
    "minio_silver_bucket",
    "minio_bronze_bucket",
    "minio_quarantine_bucket",
}


def _names(source: str) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.Attribute):
            out.add(node.attr)
        elif isinstance(node, ast.Name):
            out.add(node.id)
    return out


def test_the_demo_router_cannot_reach_bronze_silver_or_the_write_session() -> None:
    src = (SRC / "api" / "routers" / "demo.py").read_text(encoding="utf-8")
    assert _names(src) & FORBIDDEN_IN_DEMO_ROUTER == set()


def test_the_demo_router_reads_artifacts_only_through_the_gold_only_s12_queries() -> None:
    src = (SRC / "api" / "routers" / "demo.py").read_text(encoding="utf-8")
    assert "queries.get_gold_artifact" in src and "queries.gold_artifacts" in src
    assert "select(Artifact" not in src and "session.execute" not in src


def test_the_name_scanner_can_fail() -> None:
    assert _names("from x import silver_client") & FORBIDDEN_IN_DEMO_ROUTER == {"silver_client"}
    assert _names("y = settings.minio_silver_bucket") & FORBIDDEN_IN_DEMO_ROUTER == {
        "minio_silver_bucket"
    }
    assert _names("x = 1") & FORBIDDEN_IN_DEMO_ROUTER == set()


UNSAFE_JS = [
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
]


VENDORED = {"three.module.min.js", "three.core.js"}
# the SVG namespace is an identifier (needed by the icon sprite and createElementNS), not a request
SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _own(*suffixes: str) -> list[Path]:
    """Every page file we wrote (not the vendored viewer); new files are scanned automatically."""
    return sorted(p for p in STATIC.rglob("*") if p.suffix in suffixes and p.name not in VENDORED)


PAGE_FILES = _own(".html", ".css", ".js", ".svg")
SCRIPTS = _own(".js")


def test_the_scans_cover_the_landing_page() -> None:
    # the console became the Phantom app (viewer/, scanned by test_viewer_serving.py)
    names = {p.name for p in PAGE_FILES}
    assert {"landing.html", "landing.js", "scan-scene.js", "icons.svg"} <= names
    assert {p.name for p in SCRIPTS} >= {"landing.js", "scan-scene.js"}


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
@pytest.mark.parametrize("token", UNSAFE_JS)
def test_the_page_scripts_never_inject_markup(token: str, path: Path) -> None:
    assert token not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", PAGE_FILES, ids=lambda p: p.name)
def test_the_pages_have_no_inline_event_handlers_and_no_external_requests(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+\s*=", text), f"{path.name} has an inline handler"
    text = text.replace(SVG_NAMESPACE, "")
    assert "http://" not in text and "https://" not in text, (
        f"{path.name} makes an external request"
    )
    assert not re.search(r"""(?:src|href)\s*=\s*["']//""", text), (
        f"{path.name} has a protocol-relative URL"
    )


def test_the_fonts_and_icons_are_vendored_with_their_licences() -> None:
    for font in ("Geist-Variable.woff2", "GeistMono-Variable.woff2"):
        assert (STATIC / "fonts" / font).read_bytes()[:4] == b"wOF2", font
    assert (
        "SIL OPEN FONT LICENSE"
        in (STATIC / "fonts" / "OFL.txt").read_text(encoding="utf-8").upper()
    )
    assert "MIT License" in (STATIC / "PHOSPHOR-LICENSE.txt").read_text(encoding="utf-8")
    assert (STATIC / "icons.svg").read_text(encoding="utf-8").count("<symbol ") >= 10


def test_every_icon_the_pages_use_exists_in_the_sprite() -> None:
    sprite = (STATIC / "icons.svg").read_text(encoding="utf-8")
    defined = set(re.findall(r'<symbol id="(i-[a-z-]+)"', sprite))
    used: set[str] = set()
    for path in PAGE_FILES:
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"icons\.svg#(i-[a-z-]+)", text))
        used |= {f"i-{n}" for n in re.findall(r"""\bicon\(\s*["']([a-z-]+)["']\s*\)""", text)}
    assert used, "the scan found no icon references at all"
    assert used <= defined, sorted(used - defined)


@pytest.mark.parametrize("name", ["landing.html", "landing.js"])
def test_visible_copy_uses_no_em_or_en_dashes(name: str) -> None:
    text = (STATIC / name).read_text(encoding="utf-8")
    assert "—" not in text and "–" not in text


def test_three_js_is_vendored_with_its_licence() -> None:
    vendor = STATIC / "vendor"
    assert "three.js authors" in (vendor / "THREE-LICENSE.txt").read_text(encoding="utf-8")
    module = (vendor / "three.module.min.js").read_text(encoding="utf-8").replace(" ", "")
    assert 'from"./three.core.js"' in module  # its only import is vendored too
    assert (vendor / "three.core.js").stat().st_size > 100_000


def test_the_scan_point_cloud_matches_its_header() -> None:
    meta = json.loads((STATIC / "scan" / "head_points.json").read_text(encoding="utf-8"))
    blob = (STATIC / "scan" / "head_points.bin").read_bytes()
    n = meta["count"]
    assert meta["bytes_per_point"] == 12 and n == sum(meta["counts"].values())
    assert len(blob) == meta["bytes"] == n * 12 and len(blob) < 4_000_000
    raw = blob[n * 6 : n * 6 + 3000]
    normals = [b - 256 if b > 127 else b for b in raw]  # int8
    lengths = [sum(v * v for v in normals[k : k + 3]) ** 0.5 / 127 for k in range(0, 3000, 3)]
    assert all(0.9 < length < 1.1 for length in lengths)  # stored normals are unit vectors
    tissue = blob[n * 9 : n * 10]
    assert set(tissue) == {0, 1} and tissue.count(0) == meta["counts"]["skin"]
    assert meta["provenance"]["source_sha256"] == SLICER_CT_SHA256


def test_the_atlas_point_cloud_matches_its_header_and_ships_nothing_excluded() -> None:
    """The landing atlas is CC-BY-SA; the NC / unstated-licence parts must never ship
    (LICENSES.md): no visceral system, and no nervous point inside the cranium."""
    import struct

    meta = json.loads((STATIC / "atlas" / "atlas_points.json").read_text(encoding="utf-8"))
    blob = (STATIC / "atlas" / "atlas_points.bin").read_bytes()
    n = meta["count"]
    assert meta["bytes_per_point"] == 10 and len(blob) == meta["bytes"] == n * 10
    assert len(blob) < 4_000_000
    assert meta["systems"] == ["skeleton", "vessels", "nerves"]
    files = {s["file"] for s in meta["sources"].values()}
    assert files == {"SkeletalSystem100.glb", "CardioVascular41.glb", "NervousSystem100.glb"}
    system = blob[n * 9 : n * 10]
    assert set(system) == {0, 1, 2}
    ys = struct.unpack(f"<{n * 3}h", blob[: n * 6])[1::3]
    k = meta["extent_m"] / 32767
    cut = meta["alignment"]["nerves_below_y_m"]
    assert max(y * k for y, sid in zip(ys, system, strict=True) if sid == 2) < cut
    assert 0.9 < meta["alignment"]["scale"] < 1.1  # an adult atlas on an adult skull


def test_the_atlas_is_credited_under_its_share_alike_licence() -> None:
    credits = (STATIC / "atlas" / "CREDITS.txt").read_text(encoding="utf-8")
    assert "Z-Anatomy" in credits and "BodyParts3D" in credits and "CC-BY-SA 4.0" in credits
    landing = (STATIC / "landing.html").read_text(encoding="utf-8")
    assert "Z-Anatomy" in landing and "/static/atlas/CREDITS.txt" in landing


def test_the_real_scan_is_credited_with_its_unrestricted_use_statement() -> None:
    credits = (STATIC / "scan" / "CREDITS.txt").read_text(encoding="utf-8")
    assert "CT-MR Brain" in credits and SLICER_CT_SHA256 in credits
    assert "to be used without" in credits and "any restrictions" in credits
    assert "CT-MR Brain" in (STATIC / "landing.html").read_text(encoding="utf-8")


def test_the_scanner_regexes_can_fail() -> None:
    assert re.search(r"\son[a-z]+\s*=", '<button onclick="x()">')
    assert not re.search(r"\son[a-z]+\s*=", '<button class="on">')


def _published_ports(compose: str) -> list[str]:
    return re.findall(r"^\s+ports:\s*\[(.*?)\]", compose, flags=re.M)


def test_every_published_port_is_bound_to_loopback() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entries = [e.strip().strip('"') for line in _published_ports(compose) for e in line.split(",")]
    assert len(entries) >= 4, entries  # api, minio x2, postgres
    assert all(e.startswith("127.0.0.1:") for e in entries), entries


def test_the_api_signs_download_urls_for_the_host_a_browser_can_reach() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"MINIO_PUBLIC_ENDPOINT:\s*localhost:9000", compose)


def test_the_demo_port_is_configurable_and_setup_picks_a_free_one() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:${DEMO_PORT:-8000}:8000" in compose
    setup = (ROOT / "scripts" / "setup-demo.ps1").read_text(encoding="utf-8")
    assert "DEMO_PORT" in setup and "Test-PortFree" in setup
    assert "DEMO_PORT" in (ROOT / "scripts" / "wait-for-demo.ps1").read_text(encoding="utf-8")


def test_the_launcher_files_exist_for_a_double_click_start() -> None:
    for name in ("Start Demo.bat", "Stop Demo.bat", "Reset Demo.bat"):
        assert (ROOT / name).is_file(), name
    assert (ROOT / "scripts" / "setup-demo.ps1").is_file()
    start = (ROOT / "Start Demo.bat").read_text(encoding="utf-8")
    assert "docker compose up -d --build" in start and ".demo_url" in start


def test_the_landing_story_tells_five_chapters_over_all_twelve_stages() -> None:
    """The journey is five plain-language chapters; the twelve stage codes live in the ribbon and
    in each chapter's small stage line, and the scene's chapter table matches the page."""
    html = (STATIC / "landing.html").read_text(encoding="utf-8")
    chapters = re.findall(r'<article class="chapter" data-ch="(\d)"', html)
    assert chapters == ["1", "2", "3", "4", "5"]
    ribbon = re.findall(r'data-stage="(S\d+)"', html)
    assert ribbon == [f"S{i}" for i in range(1, 13)]
    staged = re.findall(r'<p class="ch-stages mono">([^<]+)</p>', html)
    assert (
        sorted({c for line in staged for c in re.findall(r"S\d+", line)}, key=lambda c: int(c[1:]))
        == ribbon
    )
    scene = (STATIC / "scan-scene.js").read_text(encoding="utf-8")
    table = scene[
        scene.index("export const CHAPTERS") : scene.index(
            "];", scene.index("export const CHAPTERS")
        )
    ]
    assert re.findall(r'"(S\d+)"', table) == ribbon  # every stage has its moment on the timeline
    assert (
        "stageToBeat" not in scene and "storyYaw" not in scene
    )  # the old 12-to-7 remap and swing are gone
