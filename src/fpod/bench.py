"""Per-bench lifecycle: validate, render, create, start/stop/delete, wait-for-ready."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from rich.console import Console

from fpod import __version__, services, templates
from fpod.config import FpodConfig
from fpod.errors import BenchExistsError, BenchNotFoundError, FpodError, ValidationError
from fpod.manifest import Manifest, load_manifest, manifest_path, save_manifest
from fpod.podman import container_exists, container_running, podman, podman_compose

console = Console()

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
_RESERVED = {
    "services", "traefik", "mail", "db",
    "mariadb", "redis-cache", "redis-queue", "redis-socketio",
    "mailpit", "adminer",
}

# The bench image installs no system Python (debian:bookworm-slim, nothing from
# apt) — every interpreter is a pyenv shim. Verified against frappe_docker
# images/bench/Dockerfile: PYTHON_VERSION_PREV=3.12, PYTHON_VERSION=3.14.
PYENV_SHIM_DIR = "/home/frappe/.pyenv/shims"

# Frappe v16 requires 3.14; v15 runs fine on 3.12.
BRANCH_PYTHON: dict[str, str] = {
    "version-15": f"{PYENV_SHIM_DIR}/python3.12",
    "version-16": f"{PYENV_SHIM_DIR}/python3.14",
    "develop": f"{PYENV_SHIM_DIR}/python3.14",
}
MIN_PYTHON_FOR_BRANCH: dict[str, tuple[int, int]] = {
    "version-16": (3, 14),
    "develop": (3, 14),
}

_PY_VERSION_RE = re.compile(r"python(\d+)\.(\d+)$")


def resolve_python(cfg: FpodConfig, branch: str, python: str | None) -> str:
    """Pick the container interpreter for a branch, rejecting impossible pairings.

    An explicit --python always wins, but is checked against the branch's floor so
    `--branch version-16 --python .../python3.12` fails here instead of ten minutes
    into `bench init`. With no --python the branch decides; unknown branches (forks,
    hotfix lines) fall back to the configured default.
    """
    floor = MIN_PYTHON_FOR_BRANCH.get(branch)
    if python:
        m = _PY_VERSION_RE.search(python)
        if floor and m:
            got = (int(m.group(1)), int(m.group(2)))
            if got < floor:
                raise ValidationError(
                    f"branch {branch!r} needs Python >= {floor[0]}.{floor[1]}, but "
                    f"--python {python} is {got[0]}.{got[1]}. "
                    f"Use {BRANCH_PYTHON[branch]} instead."
                )
        return python
    return BRANCH_PYTHON.get(branch) or str(cfg.bench_defaults["python"])


# Marker the entrypoint prints; we can switch on these from the log stream.
LOG_INIT_COMPLETE = "==> fpod: init complete"
LOG_STARTING_BENCH = "==> fpod: starting bench"
LOG_WEB_READY = "Running on http"  # werkzeug prints this when honcho's web is up


def validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValidationError(
            f"bench name {name!r} must match {_NAME_RE.pattern} "
            "(lowercase, start with letter, 2–31 chars)."
        )
    if name in _RESERVED:
        raise ValidationError(f"bench name {name!r} is reserved by fpod.")


# ---- Paths -------------------------------------------------------------------


def bench_dir(cfg: FpodConfig, name: str) -> Path:
    return cfg.benches_dir / name


def compose_file(cfg: FpodConfig, name: str) -> Path:
    return bench_dir(cfg, name) / "compose.yaml"


def entrypoint_file(cfg: FpodConfig, name: str) -> Path:
    return bench_dir(cfg, name) / "entrypoint.sh"


def container_name(name: str) -> str:
    return f"frappe-{name}"


def project_name(name: str) -> str:
    return f"fpod-bench-{name}"


def site_name(cfg: FpodConfig, name: str) -> str:
    return f"{name}.{cfg.base_domain}"


# ---- Rendering ---------------------------------------------------------------


def render_compose(
    cfg: FpodConfig, name: str, *, branch: str, admin_password: str, python: str | None = None
) -> str:
    return templates.render(
        "bench.compose.yaml.j2",
        fpod_version=__version__,
        name=name,
        network=cfg.network,
        bench_dir=str(bench_dir(cfg, name)),
        frappe_image=cfg.bench_defaults["frappe_image"],
        branch=branch,
        python=resolve_python(cfg, branch, python),
        mariadb_root_password=cfg.mariadb_root_password,
        admin_password=admin_password,
        site_name=site_name(cfg, name),
        developer_mode=cfg.bench_defaults.get("developer_mode", True),
    )


def render_entrypoint() -> str:
    return templates.render("entrypoint.sh.j2")


def write_files(
    cfg: FpodConfig, name: str, *, branch: str, admin_password: str, python: str | None = None
) -> None:
    bd = bench_dir(cfg, name)
    bd.mkdir(parents=True, exist_ok=True)
    compose_file(cfg, name).write_text(
        render_compose(cfg, name, branch=branch, admin_password=admin_password, python=python)
    )
    ep = entrypoint_file(cfg, name)
    ep.write_text(render_entrypoint())
    ep.chmod(0o755)


# ---- Lifecycle ---------------------------------------------------------------


def _stream_until_ready(name: str, *, timeout: int) -> None:
    """Stream container logs until we see honcho's web-ready marker, the
    container exits, or we time out. Raises FpodError on failure."""
    cn = container_name(name)
    start = time.time()
    proc = subprocess.Popen(
        ["podman", "logs", "-f", cn],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ready = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            console.print(f"  [dim]{line.rstrip()}[/]")
            if LOG_WEB_READY in line:
                ready = True
                break
            if time.time() - start > timeout:
                raise FpodError(
                    f"bench {name}: timeout after {timeout}s waiting for web to come up"
                )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not ready:
        # podman logs -f exited on its own (container stopped)
        if not container_running(cn):
            raise FpodError(
                f"bench {name}: container exited before web came up. "
                f"Run `fpod logs {name}` for the full log."
            )
        raise FpodError(f"bench {name}: log stream ended without ready marker.")


def create(
    cfg: FpodConfig,
    name: str,
    *,
    branch: str,
    admin_password: str,
    python: str | None = None,
    ready_timeout: int = 1500,
) -> Manifest:
    validate_name(name)
    bd = bench_dir(cfg, name)
    if bd.exists() and any(bd.iterdir()):
        raise BenchExistsError(
            f"bench {name!r} already exists at {bd}; "
            "use `fpod delete` to remove it first."
        )

    if not services.all_running(cfg):
        console.print("  services not all running; starting them")
        services.up(cfg)

    resolved_python = resolve_python(cfg, branch, python)
    write_files(cfg, name, branch=branch, admin_password=admin_password, python=resolved_python)
    console.print(f"  rendered: {compose_file(cfg, name)}")
    console.print(f"  rendered: {entrypoint_file(cfg, name)}")

    # Write initial manifest as a breadcrumb. State 'initializing' so a
    # failed create leaves a clear trace for `fpod delete --force` to clean.
    m = Manifest(
        name=name,
        site=site_name(cfg, name),
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        frappe_branch=branch,
        python=resolved_python,
        apps=["frappe"],
        db_name="",
        state="initializing",
    )
    save_manifest(cfg, m)

    console.print("  bringing container up (will pull frappe/bench image on first run)")
    podman_compose(cfg, project_name(name), compose_file(cfg, name), "up", "-d")

    console.print("  waiting for bench init + first web boot (this can take 10+ min)")
    _stream_until_ready(name, timeout=ready_timeout)

    m.state = "running"
    m.db_name = _read_db_name(cfg, name) or ""
    save_manifest(cfg, m)
    return m


# ---- Post-create lifecycle ---------------------------------------------------


def _read_db_name(cfg: FpodConfig, name: str) -> str | None:
    """Pull db_name out of the site's site_config.json. Returns None if absent."""
    site_config = (
        bench_dir(cfg, name)
        / "frappe-bench"
        / "sites"
        / site_name(cfg, name)
        / "site_config.json"
    )
    if not site_config.exists():
        return None
    try:
        return json.loads(site_config.read_text()).get("db_name")
    except (OSError, json.JSONDecodeError):
        return None


def _wait_http_ready(cfg: FpodConfig, name: str, *, timeout: int = 60) -> None:
    """Poll the bench's /api/method/ping via traefik until it returns 200.

    Note: we deliberately require 200 not "< 500". After `podman start`,
    traefik briefly returns its own 404 ("no route") until its docker
    provider rediscovers the container — that 404 is not readiness.
    """
    url = f"http://{site_name(cfg, name)}:{cfg.host_port}/api/method/ping"
    deadline = time.time() + timeout
    last_status: int | None = None
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=3.0, follow_redirects=False)
            last_status = r.status_code
            if r.status_code == 200:
                return
        except httpx.HTTPError as e:
            last_err = e
        time.sleep(2)
    detail = f" (last status: {last_status})" if last_status else f" (last error: {last_err})"
    raise FpodError(f"bench {name}: not ready after {timeout}s at {url}{detail}")


def start(cfg: FpodConfig, name: str, *, wait: bool = True, ready_timeout: int = 120) -> Manifest:
    """Start a bench. Idempotent — works whether container is missing, stopped,
    or already running."""
    m = load_manifest(cfg, name)

    if not services.all_running(cfg):
        console.print("  services not all running; starting them")
        services.up(cfg)

    cn = container_name(name)
    if container_running(cn):
        console.print(f"  {cn} already running")
    elif container_exists(cn):
        podman("start", cn, capture=True)
        console.print(f"  {cn} started")
    else:
        # Container removed (e.g. after `podman rm`); recreate from compose.
        podman_compose(cfg, project_name(name), compose_file(cfg, name), "up", "-d")
        console.print(f"  {cn} recreated from compose")

    if wait:
        _wait_http_ready(cfg, name, timeout=ready_timeout)

    m.state = "running"
    save_manifest(cfg, m)
    return m


def stop(cfg: FpodConfig, name: str) -> Manifest:
    """Gracefully stop the bench container. Services stay up."""
    m = load_manifest(cfg, name)
    cn = container_name(name)
    if not container_running(cn):
        console.print(f"  {cn} already stopped")
    else:
        podman("stop", cn, capture=True)
        console.print(f"  {cn} stopped")
    m.state = "stopped"
    save_manifest(cfg, m)
    return m


def restart(cfg: FpodConfig, name: str) -> Manifest:
    stop(cfg, name)
    return start(cfg, name)


def _drop_database(cfg: FpodConfig, db_name: str) -> None:
    """Drop the bench's MariaDB database and user. Best-effort: logs but
    doesn't raise on individual statement failures (idempotent cleanup)."""
    if not db_name:
        return
    sql = (
        f"DROP DATABASE IF EXISTS `{db_name}`; "
        f"DROP USER IF EXISTS `{db_name}`@`%`; "
        f"FLUSH PRIVILEGES;"
    )
    podman(
        "exec", "fpod-services_mariadb_1",
        "mariadb", "-uroot", f"-p{cfg.mariadb_root_password}",
        "-e", sql,
        capture=True, check=False,
    )


def delete(cfg: FpodConfig, name: str, *, keep_data: bool = False) -> None:
    """Tear down a bench: stop container, remove it, drop DB, remove or
    archive on-disk files."""
    try:
        m = load_manifest(cfg, name)
    except BenchNotFoundError:
        # No manifest, but the bench dir might exist from a botched create.
        if not bench_dir(cfg, name).exists():
            raise
        console.print(f"  no manifest; cleaning up orphan dir at {bench_dir(cfg, name)}")
        m = None

    cn = container_name(name)

    # 1. Drop the site database (only if we want data gone)
    if not keep_data and m is not None:
        db = m.db_name or _read_db_name(cfg, name)
        if db:
            console.print(f"  dropping database {db}")
            _drop_database(cfg, db)

    # 2. Tear down the container + compose project
    cf = compose_file(cfg, name)
    if cf.exists():
        podman_compose(cfg, project_name(name), cf, "down", "-v", capture=True, check=False)
    elif container_exists(cn):
        # Compose file vanished but container lingers — kill it directly.
        podman("rm", "-f", cn, capture=True, check=False)

    # 3. Wipe or archive the bench directory
    bd = bench_dir(cfg, name)
    if bd.exists():
        if keep_data:
            archive_root = cfg.data_dir / "archive"
            archive_root.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            dst = archive_root / f"{name}-{ts}"
            bd.rename(dst)
            console.print(f"  archived to {dst}")
        else:
            shutil.rmtree(bd)
            console.print(f"  removed {bd}")


# ---- Bench command wrappers (P5) ---------------------------------------------


def _exec_bench(cn: str, args: list[str]) -> None:
    """Run a `bench <args>` invocation inside the container, streaming output.
    Raises FpodError on non-zero exit."""
    cmd = ["podman", "exec", "-w", "/workspace/frappe-bench", cn, "bench", *args]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise FpodError(
            f"bench {' '.join(args)} failed in {cn} (exit {result.returncode})"
        )


def _site_installed_apps(cn: str, site: str) -> list[str]:
    """Return the list of apps installed on this site by parsing `bench list-apps`.
    Output is one app per line: `<name> <version> <branch>`."""
    result = subprocess.run(
        ["podman", "exec", "-w", "/workspace/frappe-bench", cn,
         "bench", "--site", site, "list-apps"],
        capture_output=True, text=True, check=False,
    )
    apps: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if line and not line.startswith("WARN"):
            apps.append(line.split()[0])
    return apps


def install_app(
    cfg: FpodConfig,
    name: str,
    app: str,
    *,
    branch: str | None = None,
    url: str | None = None,
    token: str | None = None,
) -> Manifest:
    """Fetch a Frappe app into the bench and install it on the site.

    `app` is always the canonical app name (its module / pyproject name) — used
    for the apps/<app> dir check, the list-apps check, and `install-app`. For a
    fork whose app name differs from the repo name, pass the app name here and
    the repo in `url`.

    `url` (optional): a git remote to fetch from instead of bench's shorthand
    registry — e.g. a fork like QuarkCyberSystems/erpnext_enterprise.

    `token` (optional): a GitHub PAT for a private `url`. Injected via a
    transient git `insteadOf` rewrite inside the container for the duration of
    the clone, then removed — it is never written into the bench's app files.

    Two phases, each independently idempotent:
      1. `bench get-app [--branch X] <app|url>` — clone + pip install deps.
      2. `bench --site <site> install-app <app>` — run install hooks (DB, etc.).
    """
    m = load_manifest(cfg, name)

    # Apps from bench's registry follow Frappe's own branch naming, so default to
    # the bench's branch — otherwise `install-app erp16 erpnext` silently pulls
    # erpnext's default (v15) onto a v16 site. A --url fork may not follow that
    # convention, so never guess for those.
    if branch is None and url is None:
        branch = m.frappe_branch

    cn = container_name(name)
    if not container_running(cn):
        raise FpodError(f"bench {name} must be running. `fpod start {name}` first.")

    # get-app prompts y/N on an existing apps/<name> dir and aborts on default-N
    # in non-interactive contexts. Skip it ourselves if the app is already cloned.
    app_dir = bench_dir(cfg, name) / "frappe-bench" / "apps" / app
    if app_dir.exists():
        console.print(f"  {app} already in apps/; skipping get-app phase")
    else:
        insteadof_key: str | None = None
        if url and token:
            # Transient credential: rewrite https://github.com/ to embed the
            # token, only inside this container's gitconfig, only for the clone.
            insteadof_key = f"url.https://x-access-token:{token}@github.com/.insteadOf"
            podman("exec", cn, "git", "config", "--global",
                   insteadof_key, "https://github.com/", capture=True)
        try:
            get_args = ["get-app"]
            if branch:
                get_args.extend(["--branch", branch])
            get_args.append(url or app)
            _exec_bench(cn, get_args)
        finally:
            if insteadof_key:
                podman("exec", cn, "git", "config", "--global", "--unset",
                       insteadof_key, capture=True, check=False)

    if app in _site_installed_apps(cn, m.site):
        console.print(f"  {app} already on site {m.site}; skipping install-app phase")
    else:
        _exec_bench(cn, ["--site", m.site, "install-app", app])

    if app not in m.apps:
        m.apps.append(app)
        save_manifest(cfg, m)
    return m


def migrate(cfg: FpodConfig, name: str) -> None:
    m = load_manifest(cfg, name)
    cn = container_name(name)
    if not container_running(cn):
        raise FpodError(f"bench {name} must be running. `fpod start {name}` first.")
    _exec_bench(cn, ["--site", m.site, "migrate"])


def _site_backups_dir(cfg: FpodConfig, name: str) -> Path:
    m = load_manifest(cfg, name)
    return (
        bench_dir(cfg, name)
        / "frappe-bench"
        / "sites"
        / m.site
        / "private"
        / "backups"
    )


def backup(cfg: FpodConfig, name: str, *, out_dir: Path | None = None) -> list[Path]:
    """Run `bench backup --with-files`, then copy the latest backup set out
    of the bench's private/backups/ to either out_dir or
    ~/.fpod/backups/<name>/<timestamp>/. Returns the list of copied paths."""
    m = load_manifest(cfg, name)
    cn = container_name(name)
    if not container_running(cn):
        raise FpodError(f"bench {name} must be running. `fpod start {name}` first.")

    _exec_bench(cn, ["--site", m.site, "backup", "--with-files"])

    src = _site_backups_dir(cfg, name)
    if not src.exists():
        raise FpodError(f"expected backups dir not found: {src}")

    # Filenames: <YYYYMMDD_HHMMSS>-<site_underscored>-<kind>.{sql.gz,tar}
    files = sorted(src.iterdir(), reverse=True)
    if not files:
        raise FpodError("bench backup produced no files")
    latest_ts = files[0].name.split("-", 1)[0]
    latest = [f for f in files if f.name.startswith(latest_ts)]

    dst = out_dir or (cfg.data_dir / "backups" / name / latest_ts)
    dst.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for f in latest:
        target = dst / f.name
        shutil.copy2(f, target)
        copied.append(target)
    return copied
