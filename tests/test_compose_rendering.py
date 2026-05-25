"""Smoke tests for the Jinja2 services compose template.

These don't run podman; they just confirm the template:
- renders without unresolved variables (StrictUndefined catches typos)
- contains the load-bearing fragments downstream code/users will look for
- is valid YAML
"""
from __future__ import annotations

import yaml
import pytest

from fpod import services
from fpod.config import default_config


@pytest.fixture
def cfg(tmp_path):
    c = default_config(data_dir=tmp_path)
    c.host_port = 8080
    c.base_domain = "localhost"
    return c


def test_renders_without_error(cfg):
    out = services.render(cfg)
    assert out, "render returned empty"


def test_is_valid_yaml(cfg):
    doc = yaml.safe_load(services.render(cfg))
    assert isinstance(doc, dict)
    assert "services" in doc
    assert "networks" in doc


def test_expected_services_present(cfg):
    doc = yaml.safe_load(services.render(cfg))
    names = set(doc["services"].keys())
    assert names == {
        "traefik", "mariadb",
        "redis-cache", "redis-queue", "redis-socketio",
        "mailpit", "adminer",
    }


def test_host_port_propagates(cfg):
    cfg.host_port = 9999
    out = services.render(cfg)
    assert '"9999:8080"' in out


def test_mariadb_password_propagates(cfg):
    cfg.mariadb_root_password = "PINKELEPHANT"
    out = services.render(cfg)
    assert "PINKELEPHANT" in out


def test_traefik_dashboard_label(cfg):
    cfg.base_domain = "dev.test"
    out = services.render(cfg)
    assert "Host(`traefik.dev.test`)" in out
    assert "Host(`mail.dev.test`)" in out
    assert "Host(`db.dev.test`)" in out


def test_uid_propagates_for_userns(cfg):
    # We can't override uid easily without monkeypatch; just check it's an int in output.
    import re
    out = services.render(cfg)
    m = re.search(r'userns_mode: "keep-id:uid=(\d+),gid=(\d+)"', out)
    assert m, "missing userns_mode line"
    assert m.group(1) == m.group(2), "uid != gid"


def test_socket_path_in_volume(cfg):
    cfg.socket = "/tmp/fake/podman.sock"
    out = services.render(cfg)
    assert "/tmp/fake/podman.sock:/var/run/docker.sock:ro" in out


def test_undeclared_var_raises():
    """StrictUndefined should blow up if we forget to pass a var."""
    from fpod import templates as tpl
    from jinja2 import UndefinedError
    with pytest.raises(UndefinedError):
        tpl.render("services.compose.yaml.j2")  # no ctx
