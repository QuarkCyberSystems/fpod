"""`fpod create <name>` — provision a new bench."""
from __future__ import annotations

from rich.console import Console

from fpod import bench
from fpod.config import load_config

console = Console()


def run(*, name: str, branch: str, apps: str, admin_password: str, python: str | None = None) -> None:
    cfg = load_config()
    py_note = f"  python={python}" if python else ""
    console.print(
        f"[bold]fpod create {name}[/]  branch={branch}  site={name}.{cfg.base_domain}{py_note}"
    )

    m = bench.create(cfg, name, branch=branch, admin_password=admin_password, python=python)

    url_port = f":{cfg.host_port}" if cfg.host_port != 80 else ""
    console.print()
    console.print("[green]bench ready[/]")
    console.print(f"  URL:   http://{m.site}{url_port}")
    console.print(f"  login: Administrator / {admin_password}")

    if apps:
        console.print(
            f"  [yellow]note:[/] --apps install is deferred to P5; "
            f"use `fpod install-app {name} <app>` for now."
        )
