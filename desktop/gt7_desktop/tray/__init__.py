"""The app's presence on each OS: a menu-bar item, a tray icon, or nothing.

Every tray runs on the main thread (`run()` blocks until quit) and offers
the same few calls to the rest of the app: `refresh()` when the state
changed, `alert()` for a message the user must see, and `request_quit()`
from any thread.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gt7_desktop.main import DesktopApp


def make_tray(app: DesktopApp) -> Any:
    if sys.platform == "darwin":
        try:
            from gt7_desktop.tray.macos import MacTray

            return MacTray(app)
        except ImportError:
            pass
    elif sys.platform == "win32":
        try:
            from gt7_desktop.tray.windows import WindowsTray

            return WindowsTray(app)
        except ImportError:
            pass
    from gt7_desktop.tray.null import NullTray

    return NullTray(app)
