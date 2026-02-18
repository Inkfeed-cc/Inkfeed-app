"""Shared Jinja2 template environment for Inkfeed.

All HTML templates live in this package directory and are loaded via
``PackageLoader``.  Custom filters (``hn_time``, ``format_source_date``)
are registered once when the environment is first created.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from jinja2 import Environment, PackageLoader


def _hn_time(timestamp: int | None) -> str:
    """Format a unix timestamp as ``YYYY-MM-DD HH:MM UTC``."""
    try:
        ts = int(timestamp or 0)
    except (TypeError, ValueError):
        ts = 0
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _format_source_date(date_str: str) -> str:
    """Format an ISO date string as `` (YYYY-MM-DD)`` or empty string."""
    if not date_str:
        return ""
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt.strftime(" (%Y-%m-%d)")
    except (ValueError, TypeError):
        return ""


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Return a shared Jinja2 Environment that loads from this package."""
    env = Environment(
        loader=PackageLoader("inkfeed", "templates"),
        autoescape=False,
    )
    env.filters["hn_time"] = _hn_time
    env.filters["format_source_date"] = _format_source_date
    return env


def get_template(name: str):
    """Load a template by file name from the ``inkfeed/templates/`` directory."""
    return get_environment().get_template(name)
