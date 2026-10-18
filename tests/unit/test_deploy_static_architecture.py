"""The public deployment's shape, read statically from docker-compose.prod.yml and deploy/Caddyfile
(DEPLOY.md). Locally everything is bound to loopback (test_demo_static_architecture.py); on the
server Caddy is the ONLY thing with host ports, the api runs in public mode, and the one MinIO
path reachable from the internet is GET/HEAD on the gold bucket (CLAUDE.md hard rule 3).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    pass


# Compose's `!reset []` clears a list inherited from the base file
_ComposeLoader.add_constructor("!reset", lambda loader, node: [])


def _prod() -> dict[str, Any]:
    text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    return yaml.load(text, Loader=_ComposeLoader)  # noqa: S506 -- SafeLoader subclass


def _caddyfile() -> str:
    return (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")


def test_only_caddy_publishes_host_ports_in_production() -> None:
    services = _prod()["services"]
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    for name, spec in base.items():
        if "ports" in spec:
            assert name in services and services[name].get("ports") == [], (
                f"{name} publishes ports in the base file and the prod overlay does not reset them"
            )
    assert set(services["caddy"]["ports"]) == {"80:80", "443:443", "443:443/udp"}


def test_the_api_runs_in_public_mode_behind_https_presigning() -> None:
    env = _prod()["services"]["api"]["environment"]
    assert env["VESALIUS_PUBLIC"] == "1"
    assert env["MINIO_PUBLIC_SECURE"] == "true"
    assert env["MINIO_PUBLIC_ENDPOINT"] == "files.${VESALIUS_DOMAIN}"
    # both refuse to start empty rather than run an admin login with no password or secret
    assert ":?" in env["VESALIUS_ADMIN_PASSWORD_HASH"]
    assert ":?" in env["VESALIUS_SESSION_SECRET"]


def test_the_api_trusts_forwarded_headers_so_the_login_rate_limit_is_per_visitor() -> None:
    command = _prod()["services"]["api"]["command"]
    assert "--proxy-headers" in command and "--forwarded-allow-ips" in command


def test_the_files_host_serves_only_reads_of_the_gold_bucket() -> None:
    caddy = _caddyfile()
    block = caddy[caddy.index("files.{$VESALIUS_DOMAIN} {") :]
    assert re.search(r"method GET HEAD OPTIONS\s+path /gold/\*", block)
    assert "handle @gold" in block and "reverse_proxy minio:9000" in block
    assert re.search(r"handle \{\s+respond \"Not found\" 404", block)
    for zone in ("bronze", "silver", "quarantine"):
        assert f"/{zone}" not in block
    # the MinIO console port is never proxied anywhere
    assert "9001" not in caddy


def test_the_app_host_has_a_content_security_policy_and_hsts() -> None:
    caddy = _caddyfile()
    assert "Strict-Transport-Security" in caddy
    assert "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'" in caddy
    assert "frame-ancestors 'none'" in caddy
