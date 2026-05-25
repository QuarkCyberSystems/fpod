"""Tests for the bench compose template and entrypoint script template."""
from __future__ import annotations

import re

import pytest
import yaml

from fpod import bench, templates as tpl
from fpod.config import default_config
from fpod.errors import ValidationError


# ---- validate_name -----------------------------------------------------------


def test_validate_accepts_simple():
    bench.validate_name("demo")
    bench.validate_name("my-bench-2")


@pytest.mark.parametrize("bad", [
    "Demo",           # uppercase
    "1demo",          # starts with digit
    "demo_",          # underscore not allowed
    "a",              # too short
    "a" * 32,         # too long
    "demo.localhost", # dots not allowed
    "",
])
def test_validate_rejects_bad(bad):
    with pytest.raises(ValidationError):
        bench.validate_name(bad)


@pytest.mark.parametrize("reserved", ["services", "mariadb", "redis-cache", "mailpit", "traefik"])
def test_validate_rejects_reserved(reserved):
    with pytest.raises(ValidationError):
        bench.validate_name(reserved)


# ---- compose template --------------------------------------------------------


@pytest.fixture
def cfg(tmp_path):
    return default_config(data_dir=tmp_path)


def test_compose_is_valid_yaml(cfg):
    out = bench.render_compose(cfg, "demo", branch="version-16", admin_password="hunter2")
    doc = yaml.safe_load(out)
    assert "frappe" in doc["services"]


def test_compose_has_external_network(cfg):
    doc = yaml.safe_load(bench.render_compose(cfg, "demo", branch="version-16", admin_password="x"))
    assert doc["networks"]["default"]["external"] is True
    assert doc["networks"]["default"]["name"] == cfg.network


def test_compose_traefik_labels(cfg):
    out = bench.render_compose(cfg, "demo", branch="version-16", admin_password="x")
    assert "traefik.enable=true" in out
    assert "Host(`demo.localhost`)" in out
    assert "PathPrefix(`/socket.io`)" in out
    assert "loadbalancer.server.port=8000" in out
    assert "loadbalancer.server.port=9000" in out


def test_compose_userns_keep_id(cfg):
    out = bench.render_compose(cfg, "demo", branch="version-16", admin_password="x")
    assert 'userns_mode: "keep-id:uid=1000,gid=1000"' in out


def test_compose_bind_mounts_bench_dir(cfg):
    out = bench.render_compose(cfg, "demo", branch="version-16", admin_password="x")
    expected = f"{bench.bench_dir(cfg, 'demo')}:/workspace"
    assert expected in out


def test_compose_environment_propagates_secrets(cfg):
    cfg.mariadb_root_password = "PINKELEPHANT"
    out = bench.render_compose(cfg, "demo", branch="version-16", admin_password="hunter2")
    assert "PINKELEPHANT" in out
    assert "hunter2" in out
    assert "version-16" in out
    assert "demo.localhost" in out


def test_compose_branch_override(cfg):
    out = bench.render_compose(cfg, "old", branch="version-15-hotfix", admin_password="x")
    assert "version-15-hotfix" in out


# ---- entrypoint template -----------------------------------------------------


def test_entrypoint_renders():
    out = bench.render_entrypoint()
    assert out.startswith("#!/bin/bash")


def test_entrypoint_uses_env_vars():
    out = bench.render_entrypoint()
    for var in ["BENCH_BRANCH", "BENCH_PYTHON", "MARIADB_ROOT_PASS", "ADMIN_PASS", "SITE_NAME", "DEV_MODE"]:
        assert f"${{{var}}}" in out


def test_entrypoint_short_circuits_on_flag():
    out = bench.render_entrypoint()
    assert ".fpod-initialized" in out
    assert "exec bench start" in out


def test_entrypoint_disables_local_redis_in_procfile():
    out = bench.render_entrypoint()
    assert "sed -i '/redis/d' Procfile" in out


def test_entrypoint_sets_shared_service_endpoints():
    out = bench.render_entrypoint()
    assert "redis://redis-cache:6379" in out
    assert "redis://redis-queue:6379" in out
    assert "redis://redis-socketio:6379" in out
    assert "set-config -g db_host mariadb" in out


def test_entrypoint_marker_lines_match_bench_constants():
    out = bench.render_entrypoint()
    assert bench.LOG_INIT_COMPLETE in out
    assert bench.LOG_STARTING_BENCH in out
