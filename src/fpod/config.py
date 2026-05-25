"""Global fpod configuration: load/save the ~/.fpod/config.toml file."""
from __future__ import annotations

import os
import secrets
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from fpod import __version__
from fpod.errors import ConfigError


def _default_data_dir() -> Path:
    return Path(os.environ.get("FPOD_HOME", str(Path.home() / ".fpod")))


def _default_compose_bin() -> str:
    """Locate podman-compose. Prefers fpod's own venv (sibling of the python
    running this code), falls back to PATH, finally bare name (will error
    later if truly absent)."""
    venv_bin = Path(sys.executable).parent / "podman-compose"
    if venv_bin.exists():
        return str(venv_bin)
    on_path = shutil.which("podman-compose")
    if on_path:
        return on_path
    return "podman-compose"


DEFAULT_BENCH_DEFAULTS: dict[str, object] = {
    "frappe_image": "docker.io/frappe/bench:latest",
    # Reality check (2026-05): Frappe `version-16` HEAD requires Python 3.14,
    # but docker.io/frappe/bench:latest ships only 3.11 + 3.12 (via pyenv).
    # Until a v3.14 bench image lands upstream, default to v15 — which our
    # earlier ~/frappe-stack bench validated on this image.
    "frappe_branch": "version-15",
    "python": "/usr/bin/python3.11",
    "developer_mode": True,
    "admin_password": "admin",
}

DEFAULT_SERVICES: dict[str, str] = {
    "mariadb_image": "docker.io/mariadb:11.8",
    "redis_image": "docker.io/redis:alpine",
    "traefik_image": "docker.io/traefik:v3.2",
    "mailpit_image": "docker.io/axllent/mailpit:latest",
    "adminer_image": "docker.io/adminer:latest",
}


@dataclass
class FpodConfig:
    version: str
    base_domain: str
    host_port: int
    data_dir: Path
    network: str
    socket: str
    compose_bin: str
    mariadb_root_password: str
    services: dict[str, str] = field(default_factory=dict)
    bench_defaults: dict[str, object] = field(default_factory=dict)

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.toml"

    @property
    def benches_dir(self) -> Path:
        return self.data_dir / "benches"

    @property
    def services_dir(self) -> Path:
        return self.data_dir / "services"


def default_config(data_dir: Path | None = None) -> FpodConfig:
    """Build a fresh FpodConfig with sensible defaults. Generates a random
    MariaDB root password — caller is responsible for save_config() to persist."""
    dd = data_dir or _default_data_dir()
    return FpodConfig(
        version=__version__,
        base_domain="localhost",
        host_port=80,
        data_dir=dd,
        network="fpod-net",
        socket=f"/run/user/{os.getuid()}/podman/podman.sock",
        compose_bin=_default_compose_bin(),
        mariadb_root_password=secrets.token_urlsafe(24),
        services=dict(DEFAULT_SERVICES),
        bench_defaults=dict(DEFAULT_BENCH_DEFAULTS),
    )


def load_config(data_dir: Path | None = None) -> FpodConfig:
    """Load config from ~/.fpod/config.toml. Raises ConfigError if missing."""
    dd = data_dir or _default_data_dir()
    path = dd / "config.toml"
    if not path.exists():
        raise ConfigError(
            f"fpod config not found at {path}. Run `fpod init` first."
        )
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"failed to parse {path}: {e}") from e

    try:
        services = {k: v for k, v in raw["services"].items() if k != "mariadb_root_password"}
        return FpodConfig(
            version=raw["fpod"]["version"],
            base_domain=raw["fpod"]["base_domain"],
            host_port=raw["fpod"]["host_port"],
            data_dir=Path(raw["fpod"]["data_dir"]),
            network=raw["podman"]["network"],
            socket=raw["podman"]["socket"],
            compose_bin=raw["podman"]["compose_bin"],
            mariadb_root_password=raw["services"]["mariadb_root_password"],
            services=services,
            bench_defaults=raw["bench_defaults"],
        )
    except KeyError as e:
        raise ConfigError(f"missing required key in {path}: {e}") from e


def save_config(cfg: FpodConfig) -> None:
    """Persist config to ~/.fpod/config.toml, creating the dir if needed."""
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    services_section = dict(cfg.services)
    services_section["mariadb_root_password"] = cfg.mariadb_root_password
    doc = {
        "fpod": {
            "version": cfg.version,
            "base_domain": cfg.base_domain,
            "host_port": cfg.host_port,
            "data_dir": str(cfg.data_dir),
        },
        "podman": {
            "network": cfg.network,
            "socket": cfg.socket,
            "compose_bin": cfg.compose_bin,
        },
        "services": services_section,
        "bench_defaults": cfg.bench_defaults,
    }
    tmp = cfg.config_path.with_suffix(".toml.tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(doc, f)
    tmp.replace(cfg.config_path)
