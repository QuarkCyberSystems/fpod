"""`fpod init` — first-time host setup."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from rich.console import Console

from fpod import services
from fpod.config import FpodConfig, default_config, load_config, save_config
from fpod.errors import ConfigError, FpodError, PodmanError
from fpod.podman import (
    ensure_user_socket,
    require_podman,
    socket_path,
)

console = Console()


def _unprivileged_port_start() -> int:
    try:
        result = subprocess.run(
            ["sysctl", "-n", "net.ipv4.ip_unprivileged_port_start"],
            capture_output=True, text=True, check=True,
        )
        return int(result.stdout.strip())
    except (subprocess.CalledProcessError, ValueError) as e:
        raise FpodError(f"could not read net.ipv4.ip_unprivileged_port_start: {e}") from e


def _validate_port(port: int) -> None:
    start = _unprivileged_port_start()
    if port < start:
        raise FpodError(
            f"port {port} requires net.ipv4.ip_unprivileged_port_start <= {port} "
            f"(currently {start}).\n"
            f"Fix: sudo sysctl net.ipv4.ip_unprivileged_port_start={port}\n"
            f"Persist:  echo 'net.ipv4.ip_unprivileged_port_start={port}' | "
            f"sudo tee /etc/sysctl.d/99-fpod.conf\n"
            f"Or rerun: fpod init --port 8080 (any port >= {start})"
        )


def run(*, port: int, update: bool) -> None:
    console.print(f"[bold]fpod init[/] (port {port})")

    # 1. Sanity check podman
    version = require_podman()
    console.print(f"  podman: {version}")

    # 2. Validate port
    _validate_port(port)
    console.print(f"  port {port}: bindable")

    # 3. Enable podman user socket (for traefik to read)
    ensure_user_socket()
    sock = socket_path(os.getuid())
    if not sock.exists():
        raise PodmanError(f"podman socket not at expected path: {sock}")
    console.print(f"  podman socket: {sock}")

    # 4. Load or generate config
    try:
        cfg = load_config()
        console.print(f"  config: existing at {cfg.config_path}")
        # honor --port override against existing config
        if cfg.host_port != port:
            cfg.host_port = port
            save_config(cfg)
            console.print(f"  config: updated host_port -> {port}")
    except ConfigError:
        cfg = default_config()
        cfg.host_port = port
        cfg.socket = str(sock)
        save_config(cfg)
        console.print(f"  config: created at {cfg.config_path}")

    # 5. Create benches/ dir for later
    cfg.benches_dir.mkdir(parents=True, exist_ok=True)

    # 6. Ensure podman network
    services.ensure_network(cfg)
    console.print(f"  network: {cfg.network}")

    # 7. Render services compose + bring up
    services.write_compose(cfg)
    console.print(f"  rendered: {services.compose_file(cfg)}")
    console.print("  starting services (may pull images on first run)...")
    services.up(cfg, pull=update)

    console.print()
    console.print("[green]ready[/]")
    console.print(f"  dashboard: http://traefik.{cfg.base_domain}" + (f":{port}" if port != 80 else ""))
    console.print(f"  mail:      http://mail.{cfg.base_domain}" + (f":{port}" if port != 80 else ""))
    console.print(f"  adminer:   http://db.{cfg.base_domain}" + (f":{port}" if port != 80 else ""))
    console.print()
    console.print("Next: [bold]fpod create <name>[/]")
