"""Per-bench manifest persisted at ~/.fpod/benches/<name>/bench.toml.

Lightweight TOML record so the CLI can list, inspect, and check the
declared state of each bench without re-deriving it from podman every time.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

from fpod.config import FpodConfig
from fpod.errors import BenchNotFoundError, ConfigError


@dataclass
class Manifest:
    name: str
    site: str
    created: str  # ISO 8601 datetime string
    frappe_branch: str
    python: str = ""  # container interpreter path; "" for pre-0.1.1 benches
    apps: list[str] = field(default_factory=lambda: ["frappe"])
    db_name: str = ""
    state: str = "running"  # initializing | running | stopped | broken


def manifest_path(cfg: FpodConfig, name: str) -> Path:
    return cfg.benches_dir / name / "bench.toml"


def load_manifest(cfg: FpodConfig, name: str) -> Manifest:
    path = manifest_path(cfg, name)
    if not path.exists():
        raise BenchNotFoundError(f"no manifest for bench {name!r} at {path}")
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"failed to parse {path}: {e}") from e
    return Manifest(
        name=raw["name"],
        site=raw["site"],
        created=raw["created"],
        frappe_branch=raw["frappe_branch"],
        python=raw.get("python", ""),
        apps=list(raw.get("apps", ["frappe"])),
        db_name=raw.get("db_name", ""),
        state=raw.get("state", "running"),
    )


def save_manifest(cfg: FpodConfig, m: Manifest) -> None:
    path = manifest_path(cfg, m.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "name": m.name,
        "site": m.site,
        "created": m.created,
        "frappe_branch": m.frappe_branch,
        "python": m.python,
        "apps": list(m.apps),
        "db_name": m.db_name,
        "state": m.state,
    }
    tmp = path.with_suffix(".toml.tmp")
    with tmp.open("wb") as f:
        tomli_w.dump(doc, f)
    tmp.replace(path)


def list_benches(cfg: FpodConfig) -> list[Manifest]:
    """Return all benches that have a valid manifest. Silently skips
    directories under benches/ that don't contain bench.toml — those are
    half-built or hand-rolled and aren't fpod-managed."""
    if not cfg.benches_dir.exists():
        return []
    out: list[Manifest] = []
    for d in sorted(cfg.benches_dir.iterdir()):
        if not d.is_dir():
            continue
        try:
            out.append(load_manifest(cfg, d.name))
        except BenchNotFoundError:
            continue
    return out
