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
from typing import Any

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
    # the console became the Vesalius app (viewer/, scanned by test_viewer_serving.py)
    names = {p.name for p in PAGE_FILES}
    assert {"landing.html", "landing.js", "hall-scene.js", "icons.svg"} <= names
    assert {p.name for p in SCRIPTS} >= {"landing.js", "hall-scene.js"}


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
    for font in (
        "Geist-Variable.woff2",
        "GeistMono-Variable.woff2",
        "PlayfairDisplay-Italic.woff2",
    ):
        assert (STATIC / "fonts" / font).read_bytes()[:4] == b"wOF2", font
    for licence in ("OFL.txt", "OFL-PlayfairDisplay.txt"):
        text = (STATIC / "fonts" / licence).read_text(encoding="utf-8")
        assert "SIL OPEN FONT LICENSE" in text.upper(), licence
    assert "Playfair Display" in (STATIC / "fonts" / "OFL-PlayfairDisplay.txt").read_text(
        encoding="utf-8"
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


def _body() -> tuple[dict[str, Any], bytes]:
    meta = json.loads((STATIC / "body" / "body.json").read_text(encoding="utf-8"))
    return meta, (STATIC / "body" / "body.bin").read_bytes()


def test_the_statue_mesh_matches_its_header() -> None:
    """Every system's positions, normals and uint16 indices sit where body.json says, inside the
    blob, and every index names a vertex of its own system."""
    import array

    meta, blob = _body()
    assert len(blob) == meta["bytes"] and len(blob) < 4_000_000
    assert [s["name"] for s in meta["systems"]] == ["skeleton", "muscle", "vessels", "organs"]
    for s in meta["systems"]:
        v, n = s["vertices"], s["triangles"]
        assert 0 < v < 65_536 and n > 1000, s["name"]
        assert s["pos_offset"] % 2 == 0 and s["idx_offset"] % 2 == 0, s["name"]
        assert s["pos_offset"] + v * 6 <= len(blob) and s["nrm_offset"] + v * 3 <= len(blob)
        idx = array.array("H", blob[s["idx_offset"] : s["idx_offset"] + n * 6])
        assert len(idx) == n * 3 and max(idx) < v, s["name"]
        raw = blob[s["nrm_offset"] : s["nrm_offset"] + 3000]
        normals = [b - 256 if b > 127 else b for b in raw]  # int8
        lengths = [sum(c * c for c in normals[k : k + 3]) ** 0.5 / 127 for k in range(0, 3000, 3)]
        assert sum(0.9 < length < 1.1 for length in lengths) > 0.98 * len(lengths), s["name"]
    assert 0.9 < meta["alignment"]["scale"] < 1.1  # an adult atlas on an adult skull
    labels = {a["label"] for a in meta["anchors"]}
    page = (STATIC / "landing.html").read_text(encoding="utf-8")
    assert set(re.findall(r'data-anchor="([A-Za-z]+)"', page)) == labels  # every label has a point


def _build_constants() -> dict[str, Any]:
    """The licence filters of the statue build, read from its source (importing it would pin
    OpenMP threads in the test process)."""
    tree = ast.parse((ROOT / "scripts" / "build_landing_body.py").read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "attr", "") == "compile":
                found[node.targets[0].id] = re.compile(
                    "".join(ast.literal_eval(a) for a in value.args[:1])
                )
            elif node.targets[0].id == "SYSTEMS":
                found["SYSTEMS"] = ast.literal_eval(value)
    return found


def test_the_statue_ships_nothing_under_a_non_commercial_or_unstated_licence() -> None:
    """The statue is CC-BY-SA; the parts named in assets/atlas/EXCLUDED.md must never ship
    (LICENSES.md): no nervous system, no kidney or ear, and only named organs from viscera."""
    meta, _ = _body()
    c = _build_constants()
    files = {s["file"] for s in meta["systems"]}
    assert files == {"skeletal.glb", "muscular.glb", "cardiovascular.glb", "visceral.glb"}
    assert {s[1] for s in c["SYSTEMS"]} == files  # what the build reads is what ships
    excluded = (ROOT / "assets" / "atlas" / "EXCLUDED.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\w+) \| `([^`]+)` \|", excluded, flags=re.M)
    assert len(rows) >= 15
    for system, name in rows:
        if system == "nervous":
            continue  # the whole system stays out (files, above)
        assert c["EXCLUDE_ANY"].search(name), f"the build would not drop {name}"
    for organ in ("Kidney.l", "Renal pelvis.r", "Pleura", "Greater omentum"):
        assert not c["ORGANS_KEEP"].match(organ), organ
    assert c["ORGANS_KEEP"].match("Liver") and c["SKIP_SUFFIX"].search("Cranium.j")


def test_the_statue_is_credited_under_its_share_alike_licence() -> None:
    credits = (STATIC / "body" / "CREDITS.txt").read_text(encoding="utf-8")
    assert "Z-Anatomy" in credits and "BodyParts3D" in credits and "CC-BY-SA 4.0" in credits
    landing = (STATIC / "landing.html").read_text(encoding="utf-8")
    assert "Z-Anatomy" in landing and "/static/body/CREDITS.txt" in landing


def test_the_real_scan_is_credited_with_its_unrestricted_use_statement() -> None:
    """The head CT stays in static/scan/ as the statue's alignment reference
    (scripts/build_landing_body.py reads it), with its credit beside it."""
    credits = (STATIC / "scan" / "CREDITS.txt").read_text(encoding="utf-8")
    assert "CT-MR Brain" in credits and SLICER_CT_SHA256 in credits
    assert "to be used without" in credits and "any restrictions" in credits


def test_the_page_credits_only_what_it_shows() -> None:
    """The scan beats happen on the statue now: the page no longer loads the head CT, so it does
    not credit it either; every in-scene identity label has a point the scene defines."""
    page = (STATIC / "landing.html").read_text(encoding="utf-8")
    scene = (STATIC / "hall-scene.js").read_text(encoding="utf-8")
    assert "head_points" not in scene and "head_points" not in page
    assert "CT-MR Brain" not in page
    ids = re.findall(r'data-anchor="(id-[a-z]+)"', page)
    assert len(ids) == 5 and all(f'"{i}"' in scene for i in ids)


def test_every_proof_ticket_shows_a_verdict_and_names_its_test() -> None:
    page = (STATIC / "landing.html").read_text(encoding="utf-8")
    tickets = re.findall(
        r'<article class="ticket" data-proof="([a-z]+)">(.*?)</article>', page, re.S
    )
    assert [t[0] for t in tickets] == ["face", "privacy", "viewer"]
    for name, body in tickets:
        assert "data-verdict" in body, name
        test = re.search(r"tests/proofs/(test_\w+\.py)", body)
        assert test and (ROOT / "tests" / "proofs" / test.group(1)).is_file(), name


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


def test_the_landing_story_is_four_beats_and_privacy_is_one_of_them() -> None:
    """The journey is a hero and four beats (scan, privacy, AI labels, hands); the scene's chapter
    table, the page's chapters and the beat index agree, so words and pictures cannot drift."""
    html = (STATIC / "landing.html").read_text(encoding="utf-8")
    chapters = re.findall(r'<article class="chapter" data-ch="([a-z]+)"', html)
    assert chapters == ["scan", "privacy", "ai", "hands"]
    assert re.findall(r'<li data-beat="([a-z]+)"', html) == chapters
    scene = (STATIC / "hall-scene.js").read_text(encoding="utf-8")
    start = scene.index("export const CHAPTERS")
    table = scene[start : scene.index("];", start)]
    assert re.findall(r'key: "([a-z]+)"', table) == ["hero", *chapters]
    bounds = [float(x) for x in re.findall(r"(?:from|to): ([\d.]+)", table)]
    assert bounds[0] == 0.0 and bounds[-1] == 1.0 and bounds == sorted(bounds)
    # the privacy beat says what is removed, in plain words
    privacy = html[html.index('data-ch="privacy"') : html.index('data-ch="ai"')]
    assert all(w in privacy for w in ("Names", "IDs", "dates", "face"))
