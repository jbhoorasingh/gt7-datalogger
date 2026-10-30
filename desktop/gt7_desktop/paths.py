"""Where the desktop app keeps data, logs and its own prefs, per OS.

macOS paths are built directly: platformdirs honours XDG_* there, and a
shell that set XDG_DATA_HOME must not move a Mac app's data. Windows and
Linux use platformdirs with appauthor=False, whose default would otherwise
repeat the name (%LOCALAPPDATA%\\GT7 Datalogger\\GT7 Datalogger).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from gt7_desktop import APP_NAME, EXE_NAME


@dataclass(frozen=True)
class Folders:
    data: Path
    logs: Path

    @property
    def prefs(self) -> Path:
        return self.data / "desktop.json"

    @property
    def lock(self) -> Path:
        return self.data / "gt7.lock"

    @property
    def quit_request(self) -> Path:
        """Created by `--quit` on Windows, where there is no SIGTERM to send."""
        return self.data / "quit-request"

    @property
    def log_file(self) -> Path:
        return self.logs / f"{EXE_NAME}.log"


def default_folders() -> Folders:
    if sys.platform == "darwin":
        home = Path.home()
        return Folders(
            data=home / "Library" / "Application Support" / APP_NAME,
            logs=home / "Library" / "Logs" / APP_NAME,
        )
    import platformdirs

    if os.name == "nt":
        data = Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))
        return Folders(data=data, logs=data / "logs")
    return Folders(
        data=Path(platformdirs.user_data_dir(EXE_NAME, appauthor=False)),
        logs=Path(platformdirs.user_state_dir(EXE_NAME, appauthor=False)) / "logs",
    )


def folders(data_dir: Path | None = None) -> Folders:
    """The folders for this run; --data-dir puts logs inside the data folder."""
    if data_dir is not None:
        data_dir = data_dir.expanduser().resolve()
        return Folders(data=data_dir, logs=data_dir / "logs")
    return default_folders()


def create(f: Folders) -> None:
    for path in (f.data, f.logs):
        path.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            path.chmod(0o700)


def frontend_dist() -> Path | None:
    """The SPA inside a frozen bundle; None from a venv (the backend finds
    the checkout's frontend/dist itself)."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle is None:
        return None
    dist = Path(bundle) / "frontend" / "dist"
    return dist if (dist / "index.html").is_file() else None
