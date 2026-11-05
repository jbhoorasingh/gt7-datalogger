"""Sleep and the end of the OS session (#194): the Linux sleep block while a
session records, and the Windows window that hears sign-out and shutdown."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gt7_desktop import main, paths, power


class FakeProc:
    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.code: int | None = None
        self.closed = False
        self.stdin = self

    def close(self) -> None:  # stdin
        self.closed = True
        self.code = 0

    def poll(self) -> int | None:
        return self.code

    def wait(self, timeout: float | None = None) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.code = -15


def _inhibitor(found: str | None = "/usr/bin/systemd-inhibit",
               environ: dict[str, str] | None = None) -> tuple[power.SleepInhibitor,
                                                              list[FakeProc]]:
    started: list[FakeProc] = []

    def popen(args: list[str], **kwargs: Any) -> FakeProc:
        started.append(FakeProc(args, **kwargs))
        return started[-1]

    inhibitor = power.SleepInhibitor(which=lambda name: found, popen=popen,
                                     environ=environ or {})
    return inhibitor, started


def test_the_sleep_block_is_taken_once_and_let_go() -> None:
    inhibitor, started = _inhibitor()
    inhibitor.hold(True)
    inhibitor.hold(True)  # the next 2 s poll: still the same block
    assert len(started) == 1
    assert started[0].args == [
        "/usr/bin/systemd-inhibit", "--what=sleep", "--mode=block",
        "--who=GT7 Datalogger", "--why=Recording a GT7 session", "cat",
    ]
    assert started[0].kwargs["stdin"] is subprocess.PIPE
    assert inhibitor.held
    inhibitor.hold(False)
    assert started[0].closed  # cat's pipe closed: logind lets go
    assert not inhibitor.held
    inhibitor.hold(True)  # the next session
    assert len(started) == 2


def test_the_inhibitor_runs_with_the_hosts_library_path() -> None:
    inhibitor, started = _inhibitor(environ={
        "LD_LIBRARY_PATH": "/tmp/_MEI123", "LD_LIBRARY_PATH_ORIG": "/usr/local/lib",
        "_PYI_ARCHIVE_FILE": "x", "APPIMAGE": "/home/me/GT7.AppImage", "HOME": "/home/me",
    })
    inhibitor.hold(True)
    env = started[0].kwargs["env"]
    assert env["LD_LIBRARY_PATH"] == "/usr/local/lib"
    assert "APPIMAGE" not in env and "_PYI_ARCHIVE_FILE" not in env
    assert env["HOME"] == "/home/me"


def test_no_systemd_inhibit_is_asked_for_once_per_session() -> None:
    inhibitor, started = _inhibitor(found=None)
    calls: list[str] = []
    inhibitor._which = lambda name: calls.append(name) or None  # type: ignore[method-assign]
    inhibitor.hold(True)
    inhibitor.hold(True)
    assert calls == ["systemd-inhibit"] and started == []
    inhibitor.hold(False)
    inhibitor.hold(True)
    assert calls == ["systemd-inhibit", "systemd-inhibit"]


def test_a_refused_block_is_not_asked_for_again_until_the_next_session() -> None:
    inhibitor, started = _inhibitor()
    inhibitor.hold(True)
    started[0].code = 1  # polkit said no
    inhibitor.hold(True)
    inhibitor.hold(True)
    assert len(started) == 1 and not inhibitor.held
    inhibitor.hold(False)
    inhibitor.hold(True)
    assert len(started) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell stand-in")
def test_the_block_ends_with_its_pipe(tmp_path: Path) -> None:
    # A stand-in systemd-inhibit that runs its command the way the real one
    # does: the command is the real cat, reading the pipe this process holds.
    fake = tmp_path / "systemd-inhibit"
    fake.write_text('#!/bin/sh\nshift 4\nexec "$@"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    inhibitor = power.SleepInhibitor(which=lambda name: str(fake), environ=dict(os.environ))
    inhibitor.hold(True)
    proc = inhibitor._proc
    assert proc is not None and proc.poll() is None
    inhibitor.release()
    assert proc.wait(5) == 0  # EOF on the pipe ended it


class Sup:
    def __init__(self, status: dict[str, Any], stop_seconds: float = 0.0) -> None:
        self.state = SimpleNamespace(phase="running", status=status)
        self.stop_seconds = stop_seconds
        self.stopped = threading.Event()

    def stop(self) -> None:
        time.sleep(self.stop_seconds)
        self.stopped.set()


class Tray:
    def __init__(self) -> None:
        self.quits = 0

    def request_quit(self) -> None:
        self.quits += 1

    def refresh(self) -> None:
        pass


def _app(tmp_path: Path) -> main.DesktopApp:
    f = paths.folders(tmp_path)
    paths.create(f)
    return main.DesktopApp(main.parse([]), f)


def test_the_app_holds_off_sleep_only_while_recording(tmp_path: Path) -> None:
    app = _app(tmp_path)
    held: list[bool] = []
    app.inhibitor = SimpleNamespace(hold=held.append, release=lambda: held.append(False))
    recording = {"connected": True, "recording": True, "session_id": 7}
    app.supervisor = Sup(recording)
    app._changed(app.supervisor.state)
    app.supervisor.state.status = {"connected": True, "recording": False, "session_id": None}
    app._changed(app.supervisor.state)
    app.supervisor.state.status = recording
    app.supervisor.state.phase = "stopped"
    app._changed(app.supervisor.state)
    assert held == [True, False, False]
    app.quit()
    assert held[-1] is False


def test_the_end_of_a_windows_session_stops_the_server_before_returning(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    app.supervisor = Sup({}, stop_seconds=0.3)
    app.tray = Tray()
    app.session_ending()
    assert app.supervisor.stopped.is_set()
    assert app.tray.quits == 1


def test_the_end_of_a_windows_session_waits_for_a_quit_under_way(tmp_path: Path) -> None:
    app = _app(tmp_path)
    app.supervisor = Sup({}, stop_seconds=0.5)
    app.tray = Tray()
    quitting = threading.Thread(target=app.quit)
    quitting.start()
    time.sleep(0.1)  # the user's Quit is stopping the server
    app.session_ending()
    assert app.supervisor.stopped.is_set()
    quitting.join(5)


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows window")
def test_the_hidden_window_hears_the_end_of_the_session() -> None:
    from gt7_desktop import endsession

    events: list[str] = []

    def on_end() -> None:
        time.sleep(0.2)  # the handler must not return before this does
        events.append("end")

    watcher = endsession.SessionWatcher(on_end=on_end, on_close=lambda: events.append("close"))
    watcher.start()
    try:
        hwnd = watcher.hwnd
        assert hwnd
        send = endsession.user32.SendMessageW
        logoff = endsession.ENDSESSION_LOGOFF
        assert send(hwnd, endsession.WM_QUERYENDSESSION, 0, logoff) == 1  # never refused
        send(hwnd, endsession.WM_ENDSESSION, 0, logoff)  # cancelled: nothing to do
        assert events == []
        send(hwnd, endsession.WM_ENDSESSION, 1, logoff)
        assert events == ["end"]  # SendMessage returned only after on_end did
        send(hwnd, endsession.WM_CLOSE, 0, 0)
        assert events == ["end", "close"]
    finally:
        watcher.stop()
    assert watcher.hwnd is None
