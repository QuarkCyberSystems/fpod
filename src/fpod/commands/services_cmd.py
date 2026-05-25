"""Handlers for `fpod services *` subcommands."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from fpod import services
from fpod.config import load_config

console = Console()


def start() -> None:
    cfg = load_config()
    services.up(cfg)
    console.print("[green]services up[/]")


def stop() -> None:
    cfg = load_config()
    services.down(cfg)
    console.print("[green]services down[/]")


def restart() -> None:
    cfg = load_config()
    services.restart(cfg)
    console.print("[green]services restarted[/]")


def status() -> None:
    cfg = load_config()
    rows = services.status(cfg)
    if not rows:
        console.print("[yellow]no services containers found[/] — run `fpod init` or `fpod services start`.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("status")
    table.add_column("image", overflow="fold")
    for r in rows:
        table.add_row(r["name"], r["status"], r["image"])
    console.print(table)


def logs(service: str | None, follow: bool) -> None:
    cfg = load_config()
    services.logs(cfg, service, follow)
