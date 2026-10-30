"""Runs the server as a child of the same executable and keeps it running (#192).

The desktop architecture review measured both process models frozen on
macOS: with uvicorn on a thread inside the tray process, every injected
failure — a native crash, a wedged loop, a failed lifespan, an in-process
restart — either killed the tray or leaked the listening socket and the
database connections. With the server as a child, each of those is a child
that exits or gets killed, and this class starts another.

- The socket is bound once, here, and handed to every child, so the port
  never goes to another program between two children and a browser sees no
  refused connection across a restart.
- READY is `/api/health` answering. After it, `/api/status` every two
  seconds feeds the tray and is the liveness probe: three misses in a row
  and the child is killed and replaced.
- A crash is respawned after 1, 2, 4, 8 s; the fifth crash in a row gives
  up. Exit 3 is never retried: the server could not start (a database from
  a newer version, no room for the pre-upgrade copy) and doing it again
  changes nothing, so the tray shows the log's last lines instead.
- Exit 75 is a restart the server asked for, started at once.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXIT_STARTUP_FAILED = 3
EXIT_RESTART = 75
BACKOFF = (1.0, 2.0, 4.0, 8.0)
GIVE_UP_AFTER = 5
POLL_EVERY = 2.0
MISSES_BEFORE_KILL = 3
READY_TIMEOUT = 120.0  # a first start migrates and may copy a large database
STOP_TIMEOUT = 15.0


@dataclass
class State:
    phase: str = "starting"  # starting, running, restarting, failed, stopped
    status: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    log_tail: str = ""
    restarts: int = 0


def server_command() -> list[str]:
    """How to start this same program in server mode."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--server"]
    launcher = Path(__file__).resolve().parents[1] / "launcher.py"
    return [sys.executable, str(launcher), "--server"]


def _get_json(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            body = json.loads(response.read().decode())
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return body if isinstance(body, dict) else None


def tail(path: Path, lines: int = 30) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(fh.tell() - 16384, 0))
            text = fh.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


class _JobObject:
    """Windows: children die with the supervisor, however it dies."""

    def __init__(self) -> None:
        self.handle: Any = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        class Limits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Limits),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = kernel32.CreateJobObjectW(None, None)
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        self.handle = job

    def add(self, proc: subprocess.Popen[bytes]) -> None:
        if self.handle is None:
            return
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]


class Supervisor:
    def __init__(
        self,
        sock: socket.socket,
        env: dict[str, str],
        log_file: Path,
        on_change: Callable[[State], None] | None = None,
        command: list[str] | None = None,
    ) -> None:
        self.sock = sock
        self.port: int = sock.getsockname()[1]
        self.env = env
        self.log_file = log_file
        self.on_change = on_change
        self.command = command or server_command()
        self.state = State()
        self._proc: subprocess.Popen[bytes] | None = None
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._job = _JobObject()
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()

    # --- control ------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gt7-supervisor", daemon=True)
        self._thread.start()

    def restart(self) -> None:
        """A fresh child that re-reads its settings; the tray stays up."""
        self._send("restart")

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        self._stopping.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._send("stop")
            try:
                proc.wait(timeout)
            except subprocess.TimeoutExpired:
                log.warning("server did not stop in %.0f s; killing it", timeout)
                proc.kill()
                proc.wait(5)
        if self._thread is not None:
            self._thread.join(5)
        self._set(phase="stopped")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def _send(self, word: str) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        try:
            proc.stdin.write(word.encode() + b"\n")
            proc.stdin.flush()
        except OSError:
            pass

    def _set(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self.state, key, value)
        if self.on_change is not None:
            try:
                self.on_change(self.state)
            except Exception:  # noqa: BLE001 - the tray must not stop supervision
                log.exception("tray update failed")

    # --- the loop -----------------------------------------------------------

    def _spawn(self) -> subprocess.Popen[bytes]:
        env = {**self.env, "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        args = [*self.command, "--supervised"]
        if os.name == "nt":
            proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, env=env,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
            self._job.add(proc)
            shared = self.sock.share(proc.pid)  # type: ignore[attr-defined]
            assert proc.stdin is not None
            proc.stdin.write(f"{len(shared)}\n".encode() + shared)
            proc.stdin.flush()
            return proc
        fd = self.sock.fileno()
        return subprocess.Popen(
            [*args, "--fd", str(fd)], stdin=subprocess.PIPE, env=env, pass_fds=(fd,)
        )

    def _run(self) -> None:
        crashes = 0
        while not self._stopping.is_set():
            self._set(phase="starting" if self.state.restarts == 0 else "restarting")
            self._proc = proc = self._spawn()
            code = self._watch(proc)
            if self._stopping.is_set():
                break
            if code == EXIT_RESTART:
                self.state.restarts += 1
                crashes = 0
                continue
            if code == EXIT_STARTUP_FAILED:
                self.ready.set()
                self._set(
                    phase="failed",
                    error="The server could not start.",
                    log_tail=tail(self.log_file),
                )
                return
            crashes += 1
            if crashes >= GIVE_UP_AFTER:
                self.ready.set()
                self._set(
                    phase="failed",
                    error=f"The server stopped unexpectedly {crashes} times in a row.",
                    log_tail=tail(self.log_file),
                )
                return
            delay = BACKOFF[min(crashes - 1, len(BACKOFF) - 1)]
            log.warning("server exited with %s; starting it again in %.0f s", code, delay)
            self.state.restarts += 1
            if self._stopping.wait(delay):
                break

    def _watch(self, proc: subprocess.Popen[bytes]) -> int:
        base = f"http://127.0.0.1:{self.port}"
        deadline = time.monotonic() + READY_TIMEOUT
        # Starting: health answers, or the child exits.
        while proc.poll() is None:
            if _get_json(f"{base}/api/health", 1.0) is not None:
                break
            if time.monotonic() > deadline:
                log.error("server not ready after %.0f s; killing it", READY_TIMEOUT)
                proc.kill()
                return proc.wait()
            time.sleep(0.1)
        else:
            return proc.returncode
        self.ready.set()
        self._set(phase="running", error="", log_tail="")
        misses = 0
        while proc.poll() is None:
            if self._stopping.wait(POLL_EVERY):
                break
            status = _get_json(f"{base}/api/status", POLL_EVERY)
            if status is None:
                misses += 1
                if misses >= MISSES_BEFORE_KILL and proc.poll() is None:
                    log.error("server stopped answering; killing it")
                    proc.kill()
            else:
                misses = 0
                self._set(status=status)
        return proc.wait()
