"""Branch -> interpreter resolution.

The bench image carries no system Python: frappe_docker's images/bench/Dockerfile
is debian:bookworm-slim and installs 3.12 + 3.14 through pyenv only. These tests
pin the mapping so a wrong default can't silently reach `bench init`.
"""
from __future__ import annotations

import pytest

from fpod import bench
from fpod.config import default_config
from fpod.errors import ValidationError
from fpod.manifest import Manifest, load_manifest, save_manifest

SHIM = bench.PYENV_SHIM_DIR


@pytest.fixture
def cfg(tmp_path):
    return default_config(data_dir=tmp_path)


# ---- defaults per branch -----------------------------------------------------

@pytest.mark.parametrize(
    "branch,expected",
    [
        ("version-15", f"{SHIM}/python3.12"),
        ("version-16", f"{SHIM}/python3.14"),
        ("develop", f"{SHIM}/python3.14"),
    ],
)
def test_branch_picks_its_interpreter(cfg, branch, expected):
    assert bench.resolve_python(cfg, branch, None) == expected


def test_unknown_branch_falls_back_to_config_default(cfg):
    # Forks and hotfix lines aren't in the table; config decides.
    assert bench.resolve_python(cfg, "version-15-hotfix", None) == cfg.bench_defaults["python"]


def test_config_default_exists_in_the_image(cfg):
    # Guards the bug this replaced: /usr/bin/python3.11 is absent from the image.
    assert str(cfg.bench_defaults["python"]).startswith(SHIM)


# ---- explicit --python -------------------------------------------------------

def test_explicit_python_wins(cfg):
    custom = f"{SHIM}/python3.14"
    assert bench.resolve_python(cfg, "version-15", custom) == custom


def test_v16_rejects_too_old_interpreter(cfg):
    with pytest.raises(ValidationError, match="needs Python >= 3.14"):
        bench.resolve_python(cfg, "version-16", f"{SHIM}/python3.12")


def test_v16_accepts_314(cfg):
    py = f"{SHIM}/python3.14"
    assert bench.resolve_python(cfg, "version-16", py) == py


def test_v15_has_no_floor(cfg):
    py = f"{SHIM}/python3.12"
    assert bench.resolve_python(cfg, "version-15", py) == py


def test_unparseable_path_is_passed_through(cfg):
    # We can't infer a version from an arbitrary path, so don't pretend to.
    py = "/opt/weird/bin/mypython"
    assert bench.resolve_python(cfg, "version-16", py) == py


# ---- it actually reaches the container ---------------------------------------

def test_v16_interpreter_lands_in_compose(cfg):
    out = bench.render_compose(cfg, "erp16", branch="version-16", admin_password="x")
    assert f'BENCH_PYTHON: "{SHIM}/python3.14"' in out


def test_v15_interpreter_lands_in_compose(cfg):
    out = bench.render_compose(cfg, "erp15", branch="version-15", admin_password="x")
    assert f'BENCH_PYTHON: "{SHIM}/python3.12"' in out


# ---- manifest records it -----------------------------------------------------

def test_manifest_round_trips_python(cfg):
    py = f"{SHIM}/python3.14"
    m = Manifest(
        name="erp16", site="erp16.localhost", created="2026-09-03T00:00:00+00:00",
        frappe_branch="version-16", python=py,
    )
    save_manifest(cfg, m)
    assert load_manifest(cfg, "erp16").python == py


def test_manifest_without_python_loads_as_empty(cfg):
    # Benches created before the field existed must still load.
    m = Manifest(
        name="old", site="old.localhost", created="2026-09-01T00:00:00+00:00",
        frappe_branch="version-15",
    )
    save_manifest(cfg, m)
    path = bench.bench_dir(cfg, "old") / "bench.toml"
    path.write_text("\n".join(l for l in path.read_text().splitlines() if not l.startswith("python")))
    assert load_manifest(cfg, "old").python == ""
