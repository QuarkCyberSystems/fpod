"""CLI handlers for the `bench` command wrappers: install-app, migrate, backup."""
from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

from fpod import bench
from fpod.config import load_config

console = Console()


def install_app(name: str, app_name: str, branch: str | None, url: str | None = None) -> None:
    cfg = load_config()
    # Private URLs auth via GITHUB_TOKEN (transient — never stored in the bench).
    token = os.environ.get("GITHUB_TOKEN") or None
    src = url if url else "bench registry"
    label = f"branch={branch}" if branch else "default branch"
    auth = "  [dim](GITHUB_TOKEN set)[/]" if (url and token) else ""
    console.print(f"[bold]fpod install-app {name} {app_name}[/]  ({label}, from {src}){auth}")
    m = bench.install_app(cfg, name, app_name, branch=branch, url=url, token=token)
    console.print(f"[green]installed[/]  {app_name} on {m.site}")
    console.print(f"  apps: {', '.join(m.apps)}")


def migrate(name: str) -> None:
    cfg = load_config()
    console.print(f"[bold]fpod migrate {name}[/]")
    bench.migrate(cfg, name)
    console.print("[green]migrated[/]")


def backup(name: str, out: str | None) -> None:
    cfg = load_config()
    console.print(f"[bold]fpod backup {name}[/]")
    files = bench.backup(cfg, name, out_dir=Path(out) if out else None)
    console.print(f"[green]backed up[/] ({len(files)} files)")
    for f in files:
        size = f.stat().st_size
        console.print(f"  [dim]{size:>10,}[/] {f}")
