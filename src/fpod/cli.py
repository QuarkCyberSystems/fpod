"""fpod CLI entry point.

P1 wired stubs for every command. P2+ progressively replace stubs with real
implementations in fpod.commands.*.
"""
from __future__ import annotations

import sys

import typer
from rich.console import Console
from rich.markup import escape

from fpod import __version__
from fpod.commands import create as create_cmd
from fpod.commands import doctor as doctor_cmd
from fpod.commands import init as init_cmd
from fpod.commands import lifecycle as life_cmd
from fpod.commands import services_cmd
from fpod.commands import wrappers as wrap_cmd
from fpod.errors import FpodError

app = typer.Typer(
    name="fpod",
    help="Manage Frappe benches on rootless Podman.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
)

services_app = typer.Typer(
    help="Shared services (mariadb, redis, traefik, mailpit, adminer).",
    no_args_is_help=True,
)
app.add_typer(services_app, name="services")

console = Console()


def _todo(cmd: str, phase: str, **kwargs) -> None:
    args = " ".join(f"{k}={v!r}" for k, v in kwargs.items() if v is not None)
    console.print(f"[yellow]TODO[/] [bold]{phase}[/]: {cmd}" + (f"  [dim]{args}[/]" if args else ""))


def _handle(fn, *args, **kwargs) -> None:
    """Run a command function, render FpodError cleanly, exit non-zero on failure."""
    try:
        fn(*args, **kwargs)
    except FpodError as e:
        console.print(f"[red]error:[/] {escape(str(e))}")
        raise typer.Exit(code=1)


# ---- Lifecycle / global ------------------------------------------------------

@app.command()
def init(
    port: int = typer.Option(80, help="Host port for traefik."),
    update: bool = typer.Option(False, "--update", help="Re-pull service images."),
) -> None:
    """First-time host setup: create ~/.fpod/, podman network, services pod."""
    _handle(init_cmd.run, port=port, update=update)


@app.command()
def doctor() -> None:
    """Run health checks against the host and running stack."""
    _handle(doctor_cmd.run)


@app.command()
def version() -> None:
    """Print fpod version."""
    typer.echo(__version__)


# ---- Bench lifecycle ---------------------------------------------------------

@app.command()
def create(
    name: str = typer.Argument(..., help="Bench name (DNS-safe slug)."),
    branch: str = typer.Option("version-15", "--branch", "-b", help="Frappe branch."),
    apps: str = typer.Option("", "--apps", help="Comma-separated extra apps to install."),
    admin_password: str = typer.Option("admin", "--admin-password", help="Site admin password."),
    python: str = typer.Option(
        None, "--python",
        help="Interpreter path inside the container (e.g. /home/frappe/.pyenv/shims/python3.14 for v16). Defaults to config.",
    ),
) -> None:
    """Provision a new bench."""
    _handle(create_cmd.run, name=name, branch=branch, apps=apps, admin_password=admin_password, python=python)


@app.command()
def start(name: str = typer.Argument(..., help="Bench name.")) -> None:
    """Start a bench (boots services if needed)."""
    _handle(life_cmd.start, name)


@app.command()
def stop(name: str = typer.Argument(..., help="Bench name.")) -> None:
    """Stop a bench (services pod stays up)."""
    _handle(life_cmd.stop, name)


@app.command()
def restart(name: str = typer.Argument(..., help="Bench name.")) -> None:
    """Restart a bench."""
    _handle(life_cmd.restart, name)


@app.command(name="delete")
def delete_(
    name: str = typer.Argument(..., help="Bench name."),
    keep_data: bool = typer.Option(False, "--keep-data", help="Archive instead of wiping."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Remove a bench (drops DB, removes container, deletes directory)."""
    _handle(life_cmd.delete, name, keep_data, yes)


@app.command(name="list")
def list_() -> None:
    """List all benches with status, URL, apps."""
    _handle(life_cmd.list_benches_cmd)


# ---- Bench interaction -------------------------------------------------------

@app.command()
def shell(name: str = typer.Argument(..., help="Bench name.")) -> None:
    """Open a bash shell inside the bench container."""
    _handle(life_cmd.shell, name)


@app.command()
def code(
    name: str = typer.Argument(..., help="Bench name."),
    print_only: bool = typer.Option(
        False, "--print", help="Print the vscode-remote URI instead of launching."
    ),
) -> None:
    """Open VS Code attached to the bench container."""
    _handle(life_cmd.code, name, print_only)


@app.command()
def logs(
    name: str = typer.Argument(..., help="Bench name."),
    service: str = typer.Option(
        None, "--service", help="web|worker|socketio|schedule|watch. Default: container logs."
    ),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow log output."),
) -> None:
    """Tail logs for a bench (or a specific honcho process inside it)."""
    _handle(life_cmd.logs, name, service, follow)


# ---- Bench app management ----------------------------------------------------

@app.command(name="install-app")
def install_app(
    name: str = typer.Argument(..., help="Bench name."),
    app_name: str = typer.Argument(..., metavar="APP", help="Canonical app name (e.g. erpnext)."),
    branch: str = typer.Option(None, "--branch", "-b", help="App branch."),
    url: str = typer.Option(
        None, "--url",
        help="Git remote to fetch from (e.g. a fork). Private repos: set GITHUB_TOKEN env.",
    ),
) -> None:
    """Install a Frappe app into a bench (from bench's registry, or a git --url)."""
    _handle(wrap_cmd.install_app, name, app_name, branch, url)


@app.command()
def migrate(name: str = typer.Argument(..., help="Bench name.")) -> None:
    """Run `bench migrate` on the bench's site."""
    _handle(wrap_cmd.migrate, name)


@app.command()
def backup(
    name: str = typer.Argument(..., help="Bench name."),
    out: str = typer.Option(None, "--out", help="Destination directory."),
) -> None:
    """Back up the bench's site (DB + files)."""
    _handle(wrap_cmd.backup, name, out)


# ---- Services subcommands ----------------------------------------------------

@services_app.command("start")
def services_start() -> None:
    """Start the shared services pod."""
    _handle(services_cmd.start)


@services_app.command("stop")
def services_stop() -> None:
    """Stop the shared services pod."""
    _handle(services_cmd.stop)


@services_app.command("restart")
def services_restart() -> None:
    """Restart the shared services pod."""
    _handle(services_cmd.restart)


@services_app.command("status")
def services_status() -> None:
    """Show shared services status."""
    _handle(services_cmd.status)


@services_app.command("logs")
def services_logs(
    service: str = typer.Argument(None, help="Optional: mariadb|redis-cache|traefik|mailpit|adminer."),
    follow: bool = typer.Option(False, "-f", "--follow"),
) -> None:
    """Tail logs from one or all shared services."""
    _handle(services_cmd.logs, service, follow)


if __name__ == "__main__":
    app()
