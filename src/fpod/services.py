"""Shared services lifecycle: render compose, start/stop/restart/status/logs."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fpod import __version__, templates
from fpod.config import FpodConfig
from fpod.errors import FpodError, PodmanError
from fpod.podman import (
    container_running,
    network_create,
    network_exists,
    podman,
    podman_compose,
)

PROJECT = "fpod-services"


def compose_file(cfg: FpodConfig) -> Path:
    return cfg.services_dir / "compose.yaml"


def render(cfg: FpodConfig) -> str:
    return templates.render(
        "services.compose.yaml.j2",
        fpod_version=__version__,
        network=cfg.network,
        host_port=cfg.host_port,
        base_domain=cfg.base_domain,
        podman_socket=cfg.socket,
        mariadb_root_password=cfg.mariadb_root_password,
        services=cfg.services,
        uid=os.getuid(),
    )


def write_compose(cfg: FpodConfig) -> Path:
    cfg.services_dir.mkdir(parents=True, exist_ok=True)
    path = compose_file(cfg)
    path.write_text(render(cfg))
    return path


def ensure_network(cfg: FpodConfig) -> None:
    if not network_exists(cfg.network):
        network_create(cfg.network)


def up(cfg: FpodConfig, *, pull: bool = False) -> None:
    """Bring the services stack up. Idempotent.
    pull=True forces image refresh first."""
    ensure_network(cfg)
    path = write_compose(cfg)
    if pull:
        podman_compose(cfg, PROJECT, path, "pull")
    podman_compose(cfg, PROJECT, path, "up", "-d")


def down(cfg: FpodConfig) -> None:
    """Stop and remove all services containers."""
    path = compose_file(cfg)
    if not path.exists():
        raise FpodError(f"no services compose at {path}; run `fpod init` first.")
    podman_compose(cfg, PROJECT, path, "down")


def restart(cfg: FpodConfig) -> None:
    """Re-render compose then up; recreates containers if compose has changed."""
    write_compose(cfg)
    path = compose_file(cfg)
    podman_compose(cfg, PROJECT, path, "up", "-d", "--force-recreate")


def status(cfg: FpodConfig) -> list[dict[str, str]]:
    """Return a list of {name, status, image} for each services container."""
    result = podman(
        "ps", "-a",
        "--filter", f"label=io.podman.compose.project={PROJECT}",
        "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}",
        capture=True,
    )
    rows: list[dict[str, str]] = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t", 2)
        name = parts[0] if len(parts) > 0 else ""
        st = parts[1] if len(parts) > 1 else ""
        img = parts[2] if len(parts) > 2 else ""
        rows.append({"name": name, "status": st, "image": img})
    return rows


def logs(cfg: FpodConfig, service: str | None, follow: bool) -> None:
    """Stream logs from one or all services. Blocks until Ctrl+C (when follow)."""
    path = compose_file(cfg)
    if not path.exists():
        raise FpodError(f"no services compose at {path}; run `fpod init` first.")
    args: list[str] = ["logs"]
    if follow:
        args.append("-f")
    if service:
        args.append(service)
    # Inherit stdio for streaming; do not capture.
    try:
        podman_compose(cfg, PROJECT, path, *args)
    except KeyboardInterrupt:
        pass


def all_running(cfg: FpodConfig) -> bool:
    """True iff every expected service container is in 'running' state."""
    expected = [
        f"{PROJECT}_{name}_1"
        for name in ("traefik", "mariadb", "redis-cache", "redis-queue", "redis-socketio", "mailpit", "adminer")
    ]
    return all(container_running(n) for n in expected)
