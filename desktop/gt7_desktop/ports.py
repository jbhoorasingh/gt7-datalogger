"""Choosing the port, and telling our own server from anything else on it."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from typing import Any

FALLBACK_PORTS = range(8001, 8010)


class NoPort(Exception):
    pass


def bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if os.name == "nt":
        # The opposite of SO_REUSEADDR's meaning there: nobody may share it.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # type: ignore[attr-defined]
    else:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def health(port: int, timeout: float = 1.0) -> dict[str, Any] | None:
    """/api/health on this machine's port, when it is a GT7 Datalogger."""
    try:
        with urllib.request.urlopen(  # noqa: S310 - fixed loopback URL
            f"http://127.0.0.1:{port}/api/health", timeout=timeout
        ) as response:
            body = json.loads(response.read().decode())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if isinstance(body, dict) and body.get("app") == "gt7-datalogger":
        return body
    return None


def choose(host: str, saved: int, explicit: bool) -> tuple[socket.socket, int]:
    """The saved port, else the next free one in 8001-8009.

    An explicit --port never falls back: the user asked for that one. A busy
    port that answers as GT7 Datalogger is reported, not skipped — it is
    almost always the Docker container — so the caller can say so.
    """
    candidates = [saved] if explicit else [saved, *(p for p in FALLBACK_PORTS if p != saved)]
    for port in candidates:
        try:
            sock = bind(host, port)
            return sock, sock.getsockname()[1]
        except OSError:
            if port == saved:
                found = health(port)
                if found is not None:
                    raise AlreadyServing(port, found) from None
            if explicit:
                raise NoPort(f"port {port} is in use") from None
    raise NoPort(f"ports {saved} and 8001-8009 are all in use")


class AlreadyServing(Exception):
    def __init__(self, port: int, health: dict[str, Any]) -> None:
        super().__init__(f"GT7 Datalogger ({health.get('install_kind', '?')}) on port {port}")
        self.port = port
        self.health = health
