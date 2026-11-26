"""The one-page shareable report: a single self-contained HTML file (images inlined as base64, no
external requests) that opens in any browser and prints cleanly to PDF.

`build_report_html` is a pure function of plain data, so it is fully testable. Every piece of text
that came from the system is HTML-escaped. The report contains only what the public S12 endpoints
already serve (pseudo identifiers, quality results, gold-artifact metadata) -- never bronze, silver
or restricted data.
"""

from __future__ import annotations

import base64
import html
import io
from datetime import UTC, datetime
from typing import Any

import numpy as np
import trimesh
from PIL import Image

from pipeline.demo.explain import explain

MAX_PREVIEW_POINTS = 120_000
PREVIEW_PX = 320


def _e(x: object) -> str:
    return html.escape("" if x is None else str(x))


def _img(png: bytes | None, alt: str) -> str:
    if not png:
        return f"<p class='muted'>({_e(alt)} not available)</p>"
    return f"<img alt='{_e(alt)}' src='data:image/png;base64,{base64.b64encode(png).decode()}'>"


def render_preview(glb_bytes: bytes) -> bytes:
    """Two depth-shaded point renderings of the exported model: from the front and from the side.

    glTF is Y-up, metres, with the patient's anterior toward -Z (see S11's RAS->glTF axes), so the
    front view looks along +Z (x horizontal, y up) and the side view looks along -X (z, y).
    """
    loaded = trimesh.load(io.BytesIO(glb_bytes), file_type="glb", process=False)
    geoms: list[Any] = (
        list(loaded.geometry.values()) if isinstance(loaded, trimesh.Scene) else [loaded]
    )
    v = np.concatenate([np.asarray(g.vertices, dtype=np.float64) for g in geoms])
    if len(v) > MAX_PREVIEW_POINTS:
        v = v[:: len(v) // MAX_PREVIEW_POINTS + 1]

    def view(h: np.ndarray, up: np.ndarray, depth: np.ndarray) -> Image.Image:
        span = max(np.ptp(h), np.ptp(up), 1e-9)
        px = ((h - h.mean()) / span * (PREVIEW_PX * 0.9) + PREVIEW_PX / 2).astype(int)
        py = (PREVIEW_PX / 2 - (up - up.mean()) / span * (PREVIEW_PX * 0.9)).astype(int)
        d = (depth - depth.min()) / max(np.ptp(depth), 1e-9)
        canvas: np.ndarray = np.full((PREVIEW_PX, PREVIEW_PX), 255, dtype=np.uint8)
        order = np.argsort(-d)  # far first, so near points overwrite
        ok = (px >= 0) & (px < PREVIEW_PX) & (py >= 0) & (py < PREVIEW_PX)
        idx = order[ok[order]]
        canvas[py[idx], px[idx]] = (40 + 170 * d[idx]).astype(np.uint8)
        return Image.fromarray(canvas, "L")

    front = view(v[:, 0], v[:, 1], v[:, 2])  # near = -Z (anterior)
    side = view(-v[:, 2], v[:, 1], v[:, 0])
    sheet = Image.new("L", (PREVIEW_PX * 2 + 10, PREVIEW_PX), 255)
    sheet.paste(front, (0, 0))
    sheet.paste(side, (PREVIEW_PX + 10, 0))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


_CSS = """
body{font-family:Segoe UI,Arial,sans-serif;max-width:860px;margin:24px auto;color:#1a1a1a;line-height:1.45}
h1{font-size:26px;margin-bottom:4px} h2{font-size:18px;margin-top:26px;border-bottom:2px solid #e4e4e4;padding-bottom:4px}
.pill{display:inline-block;padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600}
.ok{background:#dff5e3;color:#14622a}.bad{background:#fde2e2;color:#8a1c1c}.warn{background:#fff1cf;color:#7a5200}
table{border-collapse:collapse;width:100%} td,th{border-bottom:1px solid #eee;padding:6px 8px;text-align:left;vertical-align:top}
.muted{color:#666;font-size:13px} img{max-width:100%;border:1px solid #ddd;border-radius:6px}
.note{background:#f4f6ff;border-left:4px solid #5a6bd8;padding:8px 12px;margin:10px 0}
@media print{body{margin:8mm}}
"""

_STATE_PILL = {
    "done": ("ok", "Done"),
    "rejected": ("bad", "Rejected"),
    "failed": ("bad", "Failed"),
    "skipped": ("warn", "Skipped"),
    "pending": ("warn", "Not run"),
    "running": ("warn", "Running"),
}


def build_report_html(data: dict[str, Any]) -> str:
    """`data` keys: run (RunStatus dict), series (S12 detail | None), quality (S12 quality | None),
    artifacts (S12 list | None), thumbnail_png, preview_png, proofs (list of card dicts),
    generated_at (datetime, optional)."""
    run = data["run"]
    when = data.get("generated_at") or datetime.now(UTC)
    outcome = run.get("outcome")
    pill = {
        "success": ("ok", "Completed"),
        "rejected": ("bad", "Rejected by design"),
        "error": ("bad", "Error"),
    }.get(outcome or "", ("warn", "In progress"))

    def _secs(v: float | None) -> str:
        return "" if v is None else f"{v:.1f}s"

    rows = []
    for st in run["steps"]:
        css, label = _STATE_PILL.get(st["state"], ("warn", st["state"]))
        rows.append(
            f"<tr><td>{_e(st['title'])}</td>"
            f"<td><span class='pill {css}'>{_e(label)}</span></td>"
            f"<td>{_e(st['detail'] or st['what'])}</td>"
            f"<td class='muted'>{_secs(st['seconds'])}</td></tr>"
        )
    steps = "".join(rows)

    series, quality = data.get("series"), data.get("quality")
    quality_html = "<p class='muted'>No catalogue entry (the scan did not get that far).</p>"
    if series and quality:
        stage_rows = "".join(
            f"<tr><td>{_e(s['stage'])}</td><td>{_e(s['latest_status'])}</td>"
            f"<td>{_e(explain(s['latest_reason']) if s['latest_reason'] else '')}</td></tr>"
            for s in quality["stages"]
        )
        quarantine = "".join(
            f"<li><b>{_e(q['stage'])}</b>: {_e(explain(q['reason_code']))}</li>"
            for q in quality["quarantine"]
        )
        quality_html = (
            f"<p>Quality verdict: <b>{_e(quality['quality_status'])}</b>. "
            f"{_e(series['instance_count'])} images, body part {_e(series['body_part'])}, "
            f"face removal {'required' if series['deface_required'] else 'not required'}. "
            f"Data contract version {_e(series['contract_version'])}.</p>"
            + (f"<ul>{quarantine}</ul>" if quarantine else "")
            + f"<table><tr><th>Stage</th><th>Latest status</th><th>Note</th></tr>{stage_rows}</table>"
        )

    def _proof_css(verdict: str | None) -> str:
        if verdict == "passed":
            return "ok"
        return "bad" if verdict in ("failed", "error") else "warn"

    proofs = data.get("proofs") or []
    proof_rows = "".join(
        f"<tr><td>{_e(p['title'])}</td>"
        f"<td><span class='pill {_proof_css(p.get('verdict'))}'>{_e(p.get('verdict') or 'not run')}</span></td>"
        f"<td>{_e(p.get('summary', ''))}</td></tr>"
        for p in proofs
    )
    if proof_rows:
        proofs_html = (
            f"<table><tr><th>Proof</th><th>Result</th><th>Detail</th></tr>{proof_rows}</table>"
        )
    else:
        proofs_html = "<p class='muted'>The proofs have not been run in this session.</p>"
    synthetic = run["dataset"] in ("head", "chest")
    scan_kind = "a <b>synthetic</b> scan" if synthetic else "the supplied scan"
    when_text = when.strftime("%Y-%m-%d %H:%M UTC")
    planted = run.get("planted") or []
    planted_html = ""
    if planted:
        items = "".join(
            f"<li>{_e(p['label'])}: <code>{_e(p['value'])}</code></li>" for p in planted
        )
        planted_html = (
            "<h2>Fake identifiers planted in this scan</h2>"
            f"<p class='muted'>All obviously fake. They were put in the scan on purpose so the privacy "
            f"step had something real to remove.</p><ul>{items}</ul>"
        )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Scan report — {_e(run["title"])}</title><style>{_CSS}</style></head><body>
<h1>{_e(run["title"])} <span class="pill {pill[0]}">{pill[1]}</span></h1>
<p class="muted">Generated {when_text} · run {_e(run["id"])}</p>
<p>{_e(run["message"])}</p>
<div class="note">This is a demonstration on {scan_kind}.
It shows that the pipeline's mechanisms work end to end. It is not evidence about clinical performance.</div>
<h2>What happened, step by step</h2>
<table><tr><th>Step</th><th>Result</th><th>What happened</th><th></th></tr>{steps}</table>
<h2>The result</h2>
<table><tr><td>{_img(data.get("preview_png"), "3D model preview (front and side)")}
<p class="muted">The 3D model, seen from the front (left) and the side (right).</p></td>
<td>{_img(data.get("thumbnail_png"), "Preview slice")}<p class="muted">One slice through the volume.</p></td></tr></table>
<h2>Quality and record</h2>{quality_html}
{planted_html}
<h2>The four proofs</h2>
{proofs_html}
<p class="muted">Private patient identifiers never leave the secure zone: this report only contains
what the public, read-only interface serves.</p>
</body></html>"""


def thumbnail_bytes_ok(png: bytes) -> bool:
    try:
        Image.open(io.BytesIO(png)).verify()
        return True
    except Exception:  # noqa: BLE001
        return False
