"""`fpod doctor` — structured health checks across host + stack."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from fpod import __version__, services
from fpod.bench import container_name, site_name
from fpod.config import FpodConfig, load_config
from fpod.errors import ConfigError
from fpod.manifest import list_benches
from fpod.podman import container_running, network_exists, socket_path

console = Console()


class Status(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""


# ---- Individual checks -------------------------------------------------------


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def check_python() -> CheckResult:
    v = sys.version_info
    if v >= (3, 11):
        return CheckResult("python", Status.PASS, f"{v.major}.{v.minor}.{v.micro}")
    return CheckResult("python", Status.FAIL, f"need >=3.11, have {v.major}.{v.minor}")


def check_podman() -> CheckResult:
    if shutil.which("podman") is None:
        return CheckResult("podman", Status.FAIL, "binary not on PATH")
    r = _run(["podman", "--version"])
    return CheckResult("podman", Status.PASS, r.stdout.strip())


def check_podman_compose(cfg: FpodConfig | None) -> CheckResult:
    path = cfg.compose_bin if cfg else "podman-compose"
    if not Path(path).exists() and shutil.which(path) is None:
        return CheckResult("podman-compose", Status.FAIL, f"not found at {path}")
    r = _run([path, "--version"])
    return CheckResult("podman-compose", Status.PASS, r.stdout.strip().splitlines()[-1] if r.stdout else "ok")


def check_podman_socket() -> CheckResult:
    sock = socket_path(os.getuid())
    if not sock.exists():
        return CheckResult("podman socket", Status.FAIL, f"{sock} missing; run `systemctl --user enable --now podman.socket`")
    r = _run(["systemctl", "--user", "is-active", "podman.socket"])
    if r.stdout.strip() != "active":
        return CheckResult("podman socket", Status.WARN, f"unit not active ({r.stdout.strip()}); socket file present")
    return CheckResult("podman socket", Status.PASS, str(sock))


def check_config() -> tuple[CheckResult, FpodConfig | None]:
    try:
        cfg = load_config()
        return CheckResult("fpod config", Status.PASS, str(cfg.config_path)), cfg
    except ConfigError as e:
        return CheckResult("fpod config", Status.FAIL, str(e)), None


def check_network(cfg: FpodConfig) -> CheckResult:
    if network_exists(cfg.network):
        return CheckResult("podman network", Status.PASS, cfg.network)
    return CheckResult("podman network", Status.FAIL, f"{cfg.network} missing; run `fpod init`")


def check_host_port(cfg: FpodConfig) -> CheckResult:
    r = _run(["sysctl", "-n", "net.ipv4.ip_unprivileged_port_start"])
    try:
        start = int(r.stdout.strip())
    except ValueError:
        return CheckResult("host port sysctl", Status.WARN, "could not read sysctl")
    if cfg.host_port < start:
        return CheckResult(
            "host port",
            Status.FAIL,
            f"port {cfg.host_port} requires sysctl <= {cfg.host_port} (currently {start})",
        )
    return CheckResult("host port", Status.PASS, f"{cfg.host_port} (sysctl start={start})")


def check_services(cfg: FpodConfig) -> list[CheckResult]:
    expected = ["traefik", "mariadb", "redis-cache", "redis-queue", "redis-socketio", "mailpit", "adminer"]
    out: list[CheckResult] = []
    for svc in expected:
        cn = f"{services.PROJECT}_{svc}_1"
        status = Status.PASS if container_running(cn) else Status.FAIL
        detail = "running" if status == Status.PASS else "not running"
        out.append(CheckResult(f"service: {svc}", status, detail))
    return out


def check_mariadb(cfg: FpodConfig) -> CheckResult:
    cn = f"{services.PROJECT}_mariadb_1"
    if not container_running(cn):
        return CheckResult("mariadb query", Status.FAIL, f"{cn} not running")
    r = _run([
        "podman", "exec", cn,
        "mariadb", "-uroot", f"-p{cfg.mariadb_root_password}",
        "-NB", "-e", "SELECT 1",
    ])
    if r.returncode == 0 and r.stdout.strip() == "1":
        return CheckResult("mariadb query", Status.PASS, "SELECT 1 → 1")
    return CheckResult("mariadb query", Status.FAIL, (r.stderr or r.stdout).strip()[:120])


def check_redis(name: str) -> CheckResult:
    cn = f"{services.PROJECT}_{name}_1"
    if not container_running(cn):
        return CheckResult(f"redis: {name}", Status.FAIL, f"{cn} not running")
    r = _run(["podman", "exec", cn, "redis-cli", "PING"])
    if r.returncode == 0 and r.stdout.strip() == "PONG":
        return CheckResult(f"redis: {name}", Status.PASS, "PONG")
    return CheckResult(f"redis: {name}", Status.FAIL, (r.stderr or r.stdout).strip()[:120])


def check_traefik(cfg: FpodConfig) -> CheckResult:
    url = f"http://traefik.{cfg.base_domain}:{cfg.host_port}/dashboard/"
    try:
        r = httpx.get(url, timeout=3.0, follow_redirects=False)
    except httpx.HTTPError as e:
        return CheckResult("traefik dashboard", Status.FAIL, f"{url} → {e}")
    if r.status_code == 200:
        return CheckResult("traefik dashboard", Status.PASS, url)
    return CheckResult("traefik dashboard", Status.WARN, f"{url} → HTTP {r.status_code}")


def check_linger() -> CheckResult:
    r = _run(["loginctl", "show-user", os.environ.get("USER", "deck"), "--property=Linger"])
    if "Linger=yes" in r.stdout:
        return CheckResult("user linger", Status.PASS, "enabled (services survive logout)")
    return CheckResult(
        "user linger",
        Status.WARN,
        "disabled — benches stop on logout. Fix: `sudo loginctl enable-linger $USER`",
    )


def check_benches(cfg: FpodConfig) -> list[CheckResult]:
    results: list[CheckResult] = []
    for m in list_benches(cfg):
        cn = container_name(m.name)
        if not container_running(cn):
            results.append(CheckResult(f"bench: {m.name}", Status.WARN, f"state={m.state}, container not running"))
            continue
        url = f"http://{m.site}:{cfg.host_port}/api/method/ping"
        try:
            r = httpx.get(url, timeout=3.0)
            if r.status_code == 200:
                results.append(CheckResult(f"bench: {m.name}", Status.PASS, f"{url} → 200"))
            else:
                results.append(CheckResult(f"bench: {m.name}", Status.WARN, f"{url} → HTTP {r.status_code}"))
        except httpx.HTTPError as e:
            results.append(CheckResult(f"bench: {m.name}", Status.WARN, f"{url} → {e}"))
    return results


# ---- Runner ------------------------------------------------------------------


def _color(status: Status) -> str:
    return {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[status.value]


def run() -> None:
    results: list[CheckResult] = []

    # Pre-config checks
    results.append(check_python())
    results.append(check_podman())
    cfg_result, cfg = check_config()
    results.append(cfg_result)
    results.append(check_podman_compose(cfg))
    results.append(check_podman_socket())
    results.append(check_linger())

    # Config-dependent checks
    if cfg is not None:
        results.append(check_network(cfg))
        results.append(check_host_port(cfg))
        results.extend(check_services(cfg))
        # Skip stack-internal checks if any service container is down
        if all(container_running(f"{services.PROJECT}_{s}_1")
               for s in ("mariadb", "redis-cache", "redis-queue", "redis-socketio", "traefik")):
            results.append(check_mariadb(cfg))
            results.append(check_redis("redis-cache"))
            results.append(check_redis("redis-queue"))
            results.append(check_redis("redis-socketio"))
            results.append(check_traefik(cfg))
        results.extend(check_benches(cfg))

    # Render
    console.print(f"[bold]fpod doctor[/]  v{__version__}")
    table = Table(show_header=True, header_style="bold")
    table.add_column("status", width=6)
    table.add_column("check")
    table.add_column("detail", overflow="fold")
    for r in results:
        table.add_row(f"[{_color(r.status)}]{r.status.value}[/]", r.name, r.detail)
    console.print(table)

    fails = [r for r in results if r.status == Status.FAIL]
    warns = [r for r in results if r.status == Status.WARN]
    if fails:
        console.print(f"[red]{len(fails)} FAIL[/], {len(warns)} WARN")
        raise typer.Exit(code=1)
    if warns:
        console.print(f"[yellow]{len(warns)} WARN[/] — non-blocking, but worth a look.")
    else:
        console.print("[green]all checks pass[/]")
