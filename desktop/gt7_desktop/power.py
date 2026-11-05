"""Linux: keep the computer from sleeping while a session records (#194).

On macOS and Windows the server holds a power assertion itself
(`app.serve._hold_power_assertion`). Linux has no such call without D-Bus
bindings, so the supervisor asks logind through `systemd-inhibit` for a
`sleep` block. Held for the whole run, that would also refuse a suspend the
user asks for (logind blocks those too), so it is held only while a session
records and let go as soon as it stops.

The inhibitor runs `cat` on a pipe from this process. If the app dies without
letting go, the pipe closes, `cat` ends, and logind drops the block with it:
the block cannot outlive the app.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from gt7_desktop import APP_NAME

log = logging.getLogger(__name__)

WHY = "Recording a GT7 session"


class SleepInhibitor:
    """A logind sleep block, taken and let go by `hold()`. Thread-safe."""

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._which = which
        self._popen = popen
        self._environ = environ
        self._proc: Any = None
        self._lock = threading.Lock()
        # Set when the block could not be taken; cleared when recording stops,
        # so a refusal is logged once per session, not on every 2 s poll.
        self._gave_up = False

    @property
    def held(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def hold(self, on: bool) -> None:
        with self._lock:
            if not on:
                self._gave_up = False
                if self._proc is not None:
                    self._let_go()
                return
            if self._gave_up:
                return
            if self._proc is not None:
                code = self._proc.poll()
                if code is None:
                    return
                # Refused (polkit) or killed: say so once, and stop asking.
                log.warning("systemd-inhibit exited with %s; sleep is not held off", code)
                self._proc = None
                self._gave_up = True
                return
            self._take()

    def release(self) -> None:
        self.hold(False)

    def _take(self) -> None:
        exe = self._which("systemd-inhibit")
        if exe is None:
            log.info("no systemd-inhibit; sleep is not held off while recording")
            self._gave_up = True
            return
        from gt7_desktop.selfupdate import relaunch_env

        # The host's own binary: none of the frozen app's library path.
        env = relaunch_env(dict(os.environ if self._environ is None else self._environ))
        try:
            self._proc = self._popen(
                [exe, "--what=sleep", "--mode=block", f"--who={APP_NAME}", f"--why={WHY}",
                 "cat"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=env,
            )
        except OSError as exc:
            log.warning("could not hold off sleep: %s", exc)
            self._gave_up = True
            return
        log.info("holding off sleep while the session records")

    def _let_go(self) -> None:
        proc, self._proc = self._proc, None
        if proc.stdin is not None:
            try:
                proc.stdin.close()  # cat sees EOF; systemd-inhibit lets go and exits
            except OSError:
                pass
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(5)
        log.info("sleep allowed again")
