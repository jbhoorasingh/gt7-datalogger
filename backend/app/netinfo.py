"""This machine as other devices on the LAN see it."""

from __future__ import annotations

import socket
import tomllib
from functools import cache
from importlib import metadata
from pathlib import Path

# A source checkout's own pyproject.toml: in development the installed
# metadata is whatever was last pip-installed, often a release behind.
_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def lan_ip() -> str:
    """This machine's LAN address (for overlay URLs and phones on the LAN)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packet is sent; just picks a route
            ip: str = s.getsockname()[0]
            return ip
    except OSError:
        return ""


@cache
def server_version() -> str:
    try:
        version: str = tomllib.loads(_PYPROJECT.read_text())["project"]["version"]
        return version
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        pass
    try:
        return metadata.version("gt7-datalogger")
    except metadata.PackageNotFoundError:
        return "unknown"
