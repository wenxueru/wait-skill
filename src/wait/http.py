"""Loopback HTTP address policy shared by the dashboard and CLI."""

from __future__ import annotations

import re


def loopback_origin(authority: str) -> str:
    """Validate an HTTP authority without resolving arbitrary hostnames."""
    match = re.fullmatch(r"(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", authority, re.IGNORECASE | re.ASCII)
    if not match:
        raise ValueError("Invalid host")
    port = int(match[2] or 80)
    if not 1 <= port <= 65535:
        raise ValueError("Invalid host")
    return f"http://{match[1].lower()}:{port}"


def preview_origin(value: str) -> str:
    """Accept only a loopback HTTP origin, with an optional trailing slash."""
    if not value.startswith("http://"):
        raise ValueError("Invalid origin")
    try:
        return loopback_origin(value[7:].removesuffix("/"))
    except ValueError as exc:
        raise ValueError("Invalid origin") from exc
