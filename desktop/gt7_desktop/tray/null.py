"""No tray: Linux in v1, and any desktop without the native toolkit.

Every launch opens the browser, and Quit lives in the dashboard's
Settings › About (or `gt7-datalogger --quit`).
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gt7_desktop.main import DesktopApp

log = logging.getLogger(__name__)


class NullTray:
    def __init__(self, app: DesktopApp) -> None:
        self.app = app
        self._done = threading.Event()

    def run(self) -> None:
        while not self._done.wait(0.5):
            sup = self.app.supervisor
            if sup is not None and sup.state.phase == "failed":
                return

    def refresh(self) -> None:
        pass

    def alert(self, title: str, message: str) -> None:
        log.error("%s: %s", title, message)

    def request_quit(self) -> None:
        self._done.set()
