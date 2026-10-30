"""macOS: a Dock icon and a menu-bar item, through PyObjC (#194).

AppKit runs on the main thread; every call from another thread goes through
AppHelper.callAfter. Quit goes through applicationShouldTerminate_ →
NSTerminateLater and replies once the server child has stopped, so Dock ›
Quit, the menu and logout all stop the server cleanly — NSApp.stop_ would
leave a process with no server behind a modal.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

import objc
from AppKit import (
    NSAlert,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSMenu,
    NSMenuItem,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

from gt7_desktop import APP_NAME, autostart

if TYPE_CHECKING:
    from gt7_desktop.main import DesktopApp

log = logging.getLogger(__name__)

NS_TERMINATE_LATER = 2


class _Delegate(NSObject):  # type: ignore[misc]
    tray: Any = None

    def applicationShouldTerminate_(self, sender: Any) -> int:
        tray = self.tray

        def stop() -> None:
            tray.app.quit()
            AppHelper.callAfter(sender.replyToApplicationShouldTerminate_, True)

        threading.Thread(target=stop, name="gt7-quit", daemon=True).start()
        return NS_TERMINATE_LATER

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app: Any, _flag: bool) -> bool:
        self.tray.app.open_dashboard()
        return True

    def tick_(self, _timer: Any) -> None:
        self.tray._update()

    @objc.python_method  # type: ignore[untyped-decorator]
    def _menu(self, title: str, action: str, key: str = "") -> Any:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        item.setTarget_(self)
        return item

    def openDashboard_(self, _sender: Any) -> None:
        self.tray.app.open_dashboard()

    def openEngineer_(self, _sender: Any) -> None:
        self.tray.app.open_dashboard("engineer")

    def toggleLan_(self, sender: Any) -> None:
        status = self.tray.app.supervisor.state.status if self.tray.app.supervisor else {}
        on = not bool(status.get("lan_access", True))
        threading.Thread(target=self.tray.app.set_lan_access, args=(on,), daemon=True).start()

    def toggleLogin_(self, sender: Any) -> None:
        autostart.set_enabled(not autostart.enabled())
        sender.setState_(1 if autostart.enabled() else 0)

    def openData_(self, _sender: Any) -> None:
        self.tray.app.open_folder("data")

    def openLogs_(self, _sender: Any) -> None:
        self.tray.app.open_folder("logs")

    def checkUpdates_(self, _sender: Any) -> None:
        self.tray.app.open_dashboard("#/settings")

    def quitApp_(self, _sender: Any) -> None:
        if self.tray.app.recording() and not self.tray.confirm(
            "A session is recording.",
            "Quitting closes it. The laps already completed are saved.",
        ):
            return
        NSApplication.sharedApplication().terminate_(None)


class MacTray:
    def __init__(self, app: DesktopApp) -> None:
        self.app = app
        self.nsapp = NSApplication.sharedApplication()
        self.nsapp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        self.delegate = _Delegate.alloc().init()
        self.delegate.tray = self
        self.nsapp.setDelegate_(self.delegate)
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.button().setTitle_("GT7")
        menu = NSMenu.alloc().init()
        self.header = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Starting…", None, "")
        self.header.setEnabled_(False)
        menu.addItem_(self.header)
        menu.addItem_(NSMenuItem.separatorItem())
        d = self.delegate
        menu.addItem_(d._menu("Open dashboard", "openDashboard:", "o"))
        menu.addItem_(d._menu("Open Race Engineer", "openEngineer:"))
        self.lan = d._menu("Let my phone/tablet connect", "toggleLan:")
        menu.addItem_(self.lan)
        login = d._menu("Start at login", "toggleLogin:")
        login.setState_(1 if autostart.enabled() else 0)
        menu.addItem_(login)
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(d._menu("Open data folder", "openData:"))
        menu.addItem_(d._menu("Open logs", "openLogs:"))
        menu.addItem_(d._menu("Check for updates", "checkUpdates:"))
        menu.addItem_(NSMenuItem.separatorItem())
        menu.addItem_(d._menu(f"Quit {APP_NAME}", "quitApp:", "q"))
        self.item.setMenu_(menu)

    def run(self) -> None:
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self.delegate, "tick:", None, True
        )
        AppHelper.runEventLoop(installInterrupt=False)

    def _update(self) -> None:
        self.header.setTitle_(self.app.status_line())
        status = self.app.supervisor.state.status if self.app.supervisor else {}
        self.lan.setState_(1 if status.get("lan_access", True) else 0)

    def refresh(self) -> None:
        AppHelper.callAfter(self._update)

    def alert(self, title: str, message: str) -> None:
        def show() -> None:
            alert = NSAlert.alloc().init()
            alert.setMessageText_(title)
            alert.setInformativeText_(message)
            alert.runModal()

        AppHelper.callAfter(show)

    def confirm(self, title: str, message: str) -> bool:
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("Quit")
        alert.addButtonWithTitle_("Cancel")
        return bool(alert.runModal() == 1000)  # NSAlertFirstButtonReturn

    def request_quit(self) -> None:
        AppHelper.callAfter(NSApplication.sharedApplication().terminate_, None)
