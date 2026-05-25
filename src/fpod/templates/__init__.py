"""Jinja2 template rendering for compose files and entrypoint scripts."""
from __future__ import annotations

from jinja2 import Environment, PackageLoader, StrictUndefined

_env = Environment(
    loader=PackageLoader("fpod", "templates"),
    autoescape=False,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def render(name: str, /, **ctx) -> str:
    """Render a template file (relative to fpod/templates/) with ctx."""
    template = _env.get_template(name)
    return template.render(**ctx)
