"""Windows: a notification-area icon through pystray (#194)."""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import TYPE_CHECKING, Any

import pystray
from PIL import Image, ImageDraw

from gt7_desktop import APP_NAME, autostart, firewall

if TYPE_CHECKING:
    from gt7_desktop.main import DesktopApp

log = logging.getLogger(__name__)


def _icon_image() -> Any:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=12, fill=(220, 38, 38, 255))
    draw.text((14, 22), "GT7", fill=(255, 255, 255, 255))
    return image


class WindowsTray:
    def __init__(self, app: DesktopApp) -> None:
        self.app = app
        item = pystray.MenuItem
        self.icon = pystray.Icon(
            "gt7-datalogger",
            _icon_image(),
            APP_NAME,
            menu=pystray.Menu(
                item(lambda _i: self.app.status_line(), None, enabled=False),
                pystray.Menu.SEPARATOR,
                item("Open dashboard", lambda: self.app.open_dashboard(), default=True),
                item("Open Race Engineer", lambda: self.app.open_dashboard("engineer")),
                item("Let my phone/tablet connect", self._toggle_lan,
                     checked=lambda _i: self._lan_on()),
                item("Start at login", self._toggle_login, checked=lambda _i: autostart.enabled()),
                pystray.Menu.SEPARATOR,
                item("Open data folder", lambda: self.app.open_folder("data")),
                item("Open logs", lambda: self.app.open_folder("logs")),
                item("Repair firewall", lambda: firewall.repair_elevated()),
                item("Check for updates", lambda: self.app.open_dashboard("#/settings")),
                pystray.Menu.SEPARATOR,
                item(f"Quit {APP_NAME}", self._quit),
            ),
        )

    def _lan_on(self) -> bool:
        sup = self.app.supervisor
        return bool(sup.state.status.get("lan_access", True)) if sup else False

    def _toggle_lan(self) -> None:
        threading.Thread(target=self.app.set_lan_access, args=(not self._lan_on(),),
                         daemon=True).start()

    def _toggle_login(self) -> None:
        autostart.set_enabled(not autostart.enabled())

    def _quit(self) -> None:
        if self.app.recording() and not self._confirm(
            "A session is recording. Quitting closes it; completed laps are saved. Quit?"
        ):
            return
        self.request_quit()

    def _confirm(self, message: str) -> bool:
        mb_yesno, mb_iconwarning, idyes = 0x4, 0x30, 6
        answer = ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            None, message, APP_NAME, mb_yesno | mb_iconwarning
        )
        return bool(answer == idyes)

    def run(self) -> None:
        self.icon.run()

    def refresh(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:  # noqa: BLE001 - not ready yet
            pass

    def alert(self, title: str, message: str) -> None:
        threading.Thread(
            target=lambda: ctypes.windll.user32.MessageBoxW(None, message, title, 0x10),  # type: ignore[attr-defined]
            daemon=True,
        ).start()

    def request_quit(self) -> None:
        def stop() -> None:
            self.app.quit()
            self.icon.stop()

        threading.Thread(target=stop, name="gt7-quit", daemon=True).start()
