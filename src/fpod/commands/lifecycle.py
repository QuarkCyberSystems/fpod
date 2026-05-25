"""CLI handlers for bench lifecycle: start, stop, restart, delete, list, shell, code, logs."""
from __future__ import annotations

import os
import shutil
import subprocess

import typer
from rich.console import Console
from rich.table import Table

from fpod import bench
from fpod.config import load_config
from fpod.errors import FpodError
from fpod.manifest import list_benches
from fpod.podman import container_running

console = Console()


def _url(cfg, m) -> str:
    suffix = f":{cfg.host_port}" if cfg.host_port != 80 else ""
    return f"http://{m.site}{suffix}"


def start(name: str) -> None:
    cfg = load_config()
    console.print(f"[bold]fpod start {name}[/]")
    m = bench.start(cfg, name)
    console.print(f"[green]ready[/]  {_url(cfg, m)}")


def stop(name: str) -> None:
    cfg = load_config()
    console.print(f"[bold]fpod stop {name}[/]")
    bench.stop(cfg, name)
    console.print("[green]stopped[/]")


def restart(name: str) -> None:
    cfg = load_config()
    console.print(f"[bold]fpod restart {name}[/]")
    m = bench.restart(cfg, name)
    console.print(f"[green]ready[/]  {_url(cfg, m)}")


def delete(name: str, keep_data: bool, yes: bool) -> None:
    cfg = load_config()
    action = "archive" if keep_data else "DELETE"
    if not yes:
        if not typer.confirm(f"{action} bench '{name}'?", default=False):
            console.print("aborted")
            raise typer.Exit(code=0)
    console.print(f"[bold]fpod delete {name}[/]  keep_data={keep_data}")
    bench.delete(cfg, name, keep_data=keep_data)
    console.print("[green]done[/]")


def list_benches_cmd() -> None:
    cfg = load_config()
    benches = list_benches(cfg)
    if not benches:
        console.print("[yellow]no benches[/] — use `fpod create <name>` to provision one.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("state")
    table.add_column("url", overflow="fold")
    table.add_column("branch")
    table.add_column("apps")
    table.add_column("created")
    for m in benches:
        runtime_state = "running" if container_running(bench.container_name(m.name)) else m.state
        color = {"running": "green", "stopped": "yellow", "initializing": "cyan", "broken": "red"}.get(runtime_state, "white")
        table.add_row(
            m.name,
            f"[{color}]{runtime_state}[/]",
            _url(cfg, m),
            m.frappe_branch,
            ",".join(m.apps),
            m.created,
        )
    console.print(table)


def shell(name: str) -> None:
    cfg = load_config()
    # validate exists
    from fpod.manifest import load_manifest
    load_manifest(cfg, name)

    cn = bench.container_name(name)
    if not container_running(cn):
        raise FpodError(f"bench {name} is not running. `fpod start {name}` first.")
    # exec replaces this process so the user lands in a real interactive shell
    os.execvp("podman", [
        "podman", "exec", "-it", cn,
        "bash", "-lc", "cd /workspace/frappe-bench && exec bash -i",
    ])


def code(name: str, print_only: bool) -> None:
    cfg = load_config()
    from fpod.manifest import load_manifest
    load_manifest(cfg, name)

    cn = bench.container_name(name)
    if not container_running(cn):
        raise FpodError(f"bench {name} is not running. `fpod start {name}` first.")

    hex_name = cn.encode().hex()
    uri = f"vscode-remote://attached-container+{hex_name}/workspace/frappe-bench"

    if print_only:
        # bare print so user can pipe / copy
        print(uri)
        return

    if not shutil.which("code"):
        console.print("[yellow]warning:[/] `code` not on PATH; printing URI instead.")
        print(uri)
        return

    subprocess.Popen(
        ["code", "--folder-uri", uri],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    console.print(f"  launched VS Code → [dim]{uri}[/]")


_HONCHO_SERVICES = {"web", "worker", "socketio", "schedule", "watch"}


def logs(name: str, service: str | None, follow: bool) -> None:
    cfg = load_config()
    from fpod.manifest import load_manifest
    load_manifest(cfg, name)

    cn = bench.container_name(name)

    if service is not None and service not in _HONCHO_SERVICES:
        raise FpodError(
            f"unknown service {service!r}; expected one of {sorted(_HONCHO_SERVICES)}"
        )

    # In dev mode, honcho writes all five services' output to the container's
    # stdout, prefixed with `<service>.<idx> |`. We grep that prefix when the
    # user asks for a specific service — no per-service log file exists.
    base = "podman logs" + (" -f" if follow else "") + f" {cn} 2>&1"
    if service:
        # Word-boundary match: " web.1 |" but not "webserver.1 |".
        cmd_string = (
            f"{base} | grep --line-buffered -E ' {service}\\.[0-9]+ +\\|'"
        )
    else:
        cmd_string = base

    if follow:
        os.execvp("bash", ["bash", "-c", cmd_string])
    else:
        subprocess.run(["bash", "-c", cmd_string])
