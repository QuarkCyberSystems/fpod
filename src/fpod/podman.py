"""Subprocess wrappers for podman and podman-compose.

Centralises invocation so error paths and stdout handling are consistent.
Streams output by default for long-running commands (pulls, compose up);
captures for short metadata queries (network exists, etc.).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from fpod.config import FpodConfig
from fpod.errors import PodmanError


def _to_str(args: Iterable[str | Path]) -> list[str]:
    return [str(a) for a in args]


def run(
    args: list[str | Path],
    *,
    check: bool = True,
    capture: bool = False,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess. By default streams stdout/stderr to the parent.
    Raises PodmanError on non-zero exit when check=True."""
    cmd = _to_str(args)
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=capture,
            text=True,
            env=env,
            cwd=cwd,
        )
    except FileNotFoundError as e:
        raise PodmanError(f"executable not found: {cmd[0]}") from e

    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip() if capture else "(see streamed output)"
        raise PodmanError(
            f"command failed (exit {result.returncode}): {' '.join(cmd)}\n{stderr}"
        )
    return result


def podman(*args: str | Path, **kwargs) -> subprocess.CompletedProcess:
    return run(["podman", *args], **kwargs)


def podman_compose(
    cfg: FpodConfig,
    project: str,
    compose_file: Path,
    *args: str,
    **kwargs,
) -> subprocess.CompletedProcess:
    """Invoke podman-compose with our standard flags (--in-pod=false to avoid
    the userns_mode conflict)."""
    return run(
        [
            cfg.compose_bin,
            "-p", project,
            "-f", compose_file,
            "--in-pod=false",
            *args,
        ],
        **kwargs,
    )


# --- High-level helpers -------------------------------------------------------


def require_podman() -> str:
    """Return the podman version string or raise if podman isn't usable."""
    if shutil.which("podman") is None:
        raise PodmanError("podman binary not found on PATH")
    result = podman("--version", capture=True)
    return result.stdout.strip()


def network_exists(name: str) -> bool:
    result = podman("network", "exists", name, capture=True, check=False)
    return result.returncode == 0


def network_create(name: str) -> None:
    podman("network", "create", name, capture=True)


def container_running(name: str) -> bool:
    result = podman(
        "container", "inspect", "--format", "{{.State.Running}}", name,
        capture=True, check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def container_exists(name: str) -> bool:
    result = podman(
        "container", "exists", name,
        capture=True, check=False,
    )
    return result.returncode == 0


def socket_path(uid: int) -> Path:
    """Default rootless podman socket path."""
    return Path(f"/run/user/{uid}/podman/podman.sock")


def ensure_user_socket() -> None:
    """Make sure the user-mode podman.socket systemd unit is active.
    Idempotent."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "podman.socket"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError as e:
        raise PodmanError("systemctl not available; cannot enable podman.socket") from e
    if result.stdout.strip() == "active":
        return
    subprocess.run(
        ["systemctl", "--user", "enable", "--now", "podman.socket"],
        check=True,
    )
