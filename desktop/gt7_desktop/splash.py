"""The splash window shown while the app starts, on macOS and Windows.

Like a desktop editor's splash, it shows the moment the app is opened and
goes away once the dashboard opens in the browser. Its five start lights
count the start: four come on at a steady pace while the server starts,
the fifth only once the server answers, then all five turn green together
while the browser opens the dashboard.

Never at login (`--background`), for a second launch (that one only opens
the dashboard), for the command-line tools, or on Linux, which has no
toolkit in the bundle; there the browser opens as soon as the server is
ready, as before.

The pictures are `desktop/icons/splash/*.png` (drawn by make_splash.py);
the status line and the version are written at run time, by the window for
each OS (splash_macos, splash_windows).
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

LIGHTS = 5
STEP = 0.3  # seconds between the first four lights
HOLD = 0.35  # all five red, before they turn green
GO = 0.8  # all green, while the browser opens
SLOW = 6.0  # past this without the server, say why it takes a while
TICK = 0.05

# The window's size in points; the frames are drawn at 2x.
WIDTH, HEIGHT = 640, 360


@dataclass(frozen=True)
class Frame:
    lit: int
    go: bool
    status: str
    done: bool

    @property
    def image(self) -> str:
        return "go.png" if self.go else f"lights-{self.lit}.png"


def frame_at(elapsed: float, ready_at: float | None) -> Frame:
    """What the splash shows `elapsed` seconds after it opened, when the
    server answered `ready_at` seconds after (None: not yet)."""
    elapsed += 1e-6  # a clock difference of 0.29999… is the 0.3 s light
    paced = min(LIGHTS - 1, int(elapsed / STEP))
    if ready_at is None:
        status = ("Still starting: the first start sets up the database"
                  if elapsed >= SLOW else "Starting the server…")
        return Frame(paced, False, status, False)
    # The fifth light is the server's: never before the pace allows it.
    fifth = max(ready_at, (LIGHTS - 1) * STEP + STEP)
    if elapsed < fifth:
        return Frame(paced, False, "Starting the server…", False)
    out = fifth + HOLD
    if elapsed < out:
        return Frame(LIGHTS, False, "Ready", False)
    return Frame(LIGHTS, True, "Go: opening the dashboard…", elapsed >= out + GO)


class View(Protocol):
    def show(self, frame: Frame, version: str) -> None: ...
    def render(self, frame: Frame) -> None: ...
    def close(self) -> None: ...


class Splash:
    """Runs the sequence on a thread of its own and drives a View.

    `ready()` is told when the server answers; `go` is called once, as the
    lights turn green (the app opens the dashboard there); `fail()` closes the
    splash at once, for an error the app is about to show.
    """

    def __init__(self, view: View, version: str, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.view = view
        self.version = version
        self.clock = clock
        self.go: Callable[[], None] = lambda: None
        self._start = 0.0
        self._ready_at: float | None = None
        self._shown: Frame | None = None
        self._went = False
        self._closed = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        self._start = self.clock()
        self._shown = frame_at(0.0, None)
        self.view.show(self._shown, self.version)
        threading.Thread(target=self._run, name="gt7-splash", daemon=True).start()

    def ready(self, go: Callable[[], None]) -> None:
        with self._lock:
            self.go = go
            if self._ready_at is None:
                self._ready_at = self.clock() - self._start

    def fail(self) -> None:
        self._close()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def tick(self) -> bool:
        """Advance to now. Returns False once the splash has closed."""
        if self._closed.is_set():
            return False
        with self._lock:
            frame = frame_at(self.clock() - self._start, self._ready_at)
            go = self.go
        if frame != self._shown:
            self._shown = frame
            self.view.render(frame)
        if frame.go and not self._went:
            self._went = True
            try:
                go()
            except Exception:  # noqa: BLE001 - the splash must still close
                log.exception("opening the dashboard failed")
        if frame.done:
            self._close()
            return False
        return True

    def _run(self) -> None:
        while self.tick():
            self._closed.wait(TICK)

    def _close(self) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self._closed.set()
        try:
            self.view.close()
        except Exception:  # noqa: BLE001
            log.exception("closing the splash failed")


def make_view() -> Any:
    """The window for this OS, or None where there is none."""
    try:
        if sys.platform == "darwin":
            from gt7_desktop.splash_macos import MacSplashView

            return MacSplashView()
        if sys.platform == "win32":
            from gt7_desktop.splash_windows import WindowsSplashView

            return WindowsSplashView()
    except Exception:  # noqa: BLE001 - a missing toolkit: start without one
        log.exception("no splash window")
    return None
