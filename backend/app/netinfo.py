"""This machine as other devices on the LAN see it."""

from __future__ import annotations

import socket

from app.version import version


def lan_ip() -> str:
    """This machine's LAN address (for overlay URLs and phones on the LAN)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packet is sent; just picks a route
            ip: str = s.getsockname()[0]
            return ip
    except OSError:
        return ""


def server_version() -> str:
    return version()
