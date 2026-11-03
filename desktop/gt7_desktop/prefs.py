"""desktop.json: the launcher's own prefs, nothing the server owns.

Port and bind only. No token, no setup flag and no app settings: those live
in the database, where the server and a restore can see them.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PORT = 8000


@dataclass
class Prefs:
    port: int = DEFAULT_PORT
    # Bind 127.0.0.1 so the OS refuses other devices outright, instead of
    # the live LAN policy deciding per request. An advanced choice (#195).
    strict_loopback: bool = False
    # Opened the dashboard on a first launch already; later launches from
    # login start in the background.
    opened_once: bool = False


def load(path: Path) -> Prefs:
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return Prefs()
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable %s: %s", path.name, exc)
        return Prefs()
    known = {k: v for k, v in raw.items() if k in Prefs.__dataclass_fields__}
    try:
        prefs = Prefs(**known)
    except TypeError:
        return Prefs()
    if not isinstance(prefs.port, int) or not 1 <= prefs.port <= 65535:
        prefs.port = DEFAULT_PORT
    return prefs


def save(path: Path, prefs: Prefs) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(prefs), indent=2) + "\n")
    os.replace(tmp, path)
