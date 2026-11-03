"""Start at login, per OS (#194). Launched that way, the app passes --login
and starts in the background instead of opening the browser."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from gt7_desktop import APP_NAME, EXE_NAME

log = logging.getLogger(__name__)

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _linux_entry() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "autostart" / f"{EXE_NAME}.desktop"


def _command() -> str:
    exe = os.environ.get("APPIMAGE") or sys.executable
    return f'"{exe}" --background --login'


def enabled() -> bool:
    try:
        if sys.platform == "darwin":
            from ServiceManagement import SMAppService

            return bool(SMAppService.mainAppService().status() == 1)  # enabled
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, APP_NAME)
            return True
        return _linux_entry().exists()
    except (ImportError, OSError):
        return False


def set_enabled(on: bool) -> bool:
    """Returns whether the change took. On macOS it needs the signed .app."""
    try:
        if sys.platform == "darwin":
            from ServiceManagement import SMAppService

            service = SMAppService.mainAppService()
            ok, error = (service.registerAndReturnError_(None) if on
                         else service.unregisterAndReturnError_(None))
            if not ok:
                log.warning("login item not changed: %s", error)
            return bool(ok)
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
                if on:
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                    except FileNotFoundError:
                        pass
            return True
        entry = _linux_entry()
        if on:
            entry.parent.mkdir(parents=True, exist_ok=True)
            entry.write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name={APP_NAME}\nExec={_command()}\nX-GNOME-Autostart-enabled=true\n"
            )
        else:
            entry.unlink(missing_ok=True)
        return True
    except (ImportError, OSError):
        log.exception("login item not changed")
        return False
