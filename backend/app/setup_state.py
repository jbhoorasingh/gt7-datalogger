"""First-run setup: the one marker that says setup is done (#184).

The marker is a settings row and the ONLY setup state: it is never derived
from sessions, laps or stored settings. An install that predates it is
spared the wizard by the SPA's redirect rule (desktop installs, or installs
with no sessions), not by guessing here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from alembic.script import ScriptDirectory

from app.storage.db import alembic_config
from app.version import version

if TYPE_CHECKING:
    from app.config import Settings
    from app.service import TelemetryService

SETUP_KEY = "setup_completed"

InstallKind = Literal["desktop", "docker", "source"]


def install_kind(settings: Settings) -> InstallKind:
    if settings.desktop:
        return "desktop"
    if Path("/.dockerenv").exists() or os.environ.get("GT7_IN_DOCKER"):
        return "docker"
    return "source"


def head_revision() -> str | None:
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


async def health(service: TelemetryService | None, settings: Settings) -> dict[str, Any]:
    current = await service.repo.schema_revision() if service is not None else None
    return {
        "status": "ok",
        "app": "gt7-datalogger",
        "version": version(),
        "install_kind": install_kind(settings),
        "schema": {"current": current, "head": head_revision()},
    }


async def is_completed(service: TelemetryService) -> bool:
    return bool((await service.repo.get_settings()).get(SETUP_KEY))


async def state(service: TelemetryService, lan_reachable: bool | None) -> dict[str, Any]:
    status = await service.status()
    running = bool(getattr(service.source, "running", True))
    return {
        "completed": await is_completed(service),
        "sessions": (await service.repo.stats()).get("sessions", 0),
        "ps_ip_saved": bool(service.settings.ps_ip),
        "source": service.settings.source,
        "listening": service.listener_error is None and running,
        "listener_error": service.listener_error,
        "heartbeat_error": status.get("heartbeat_error"),
        "lan_reachable": lan_reachable and service.settings.lan_access,
        "lan_access": service.settings.lan_access,
    }
