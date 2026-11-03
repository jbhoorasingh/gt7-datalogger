"""The server process of the desktop app: `gt7-datalogger --server` (#192).

The desktop app is one executable in three modes. The default is a small
supervisor that owns the tray and the listening socket; it starts this
module as a child of the same executable and restarts it when it dies. The
server therefore runs the way Docker runs it — uvicorn on the main thread —
and a crash, a wedge or a settings restart is the child's problem, never
the tray's. `--headless` (systemd, the Linux packages) is this same server
with no parent.

What it adds to plain uvicorn:

- **The socket comes from the parent.** The supervisor binds once and hands
  the socket down (an inherited fd on macOS and Linux, `socket.share` bytes
  on stdin on Windows), so a respawn never races another program for the
  port and the browser sees no refused connection.
- **A control pipe.** With `--supervised`, stdin carries one word per line:
  `stop`, or `restart` (exit 75, so the supervisor starts a fresh child that
  re-reads its prefs). End of file means the supervisor is gone, and the
  server stops: a tray that crashed must not leave an orphan holding the
  database.
- **One writer.** `gt7-server.lock` in the data folder is held for the life
  of the process; a second server on the same data exits 3 before touching
  the database.
- **Quit from the dashboard.** `QUIT` stops this server for good: under the
  supervisor it exits 76, and the supervisor quits the whole app; headless,
  it just stops. Settings › About calls it (`POST /api/admin/quit`) for the
  person at this computer, which on Linux, with no tray, is the only Quit.
- **Exit codes:** 0 stopped, 3 could not start (the supervisor shows the log
  tail instead of retrying), 75 restart requested, 76 quit requested.

Inert in Docker and source installs: nothing imports it unless this mode is
asked for.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import IO, Any

EXIT_OK = 0
EXIT_STARTUP_FAILED = 3
EXIT_RESTART = 75
EXIT_QUIT = 76

# Set by run() once the server exists: stops it, and under the supervisor
# the app with it. None in Docker and source installs, which never run this.
QUIT: Callable[[], None] | None = None

SERVER_LOCK = "gt7-server.lock"


class ServerLockHeld(Exception):
    pass


@contextlib.contextmanager
def server_lock(data_dir: Path) -> Iterator[None]:
    """Exclusive for the process's life; released by the OS if it dies."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle: IO[bytes] = open(data_dir / SERVER_LOCK, "a+b")  # noqa: SIM115
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ServerLockHeld(str(data_dir)) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(json.dumps({"pid": os.getpid()}).encode())
        handle.flush()
        yield
    finally:
        handle.close()


def _unblock_signals() -> None:
    """A child inherits its parent's blocked set; the supervisor blocks
    SIGTERM and SIGINT for its sigwait thread, and with them still blocked
    here uvicorn's handlers would never run."""
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGINT, signal.SIGTERM})


def _socket_from_parent(fd: int | None) -> socket.socket:
    if sys.platform == "win32":
        # The supervisor writes the length, a newline, then socket.share()'s
        # bytes, before anything else goes down the pipe.
        size = int(_read_line(0))
        data = b""
        while len(data) < size:
            chunk = os.read(0, size - len(data))
            if not chunk:
                raise OSError("control pipe closed before the socket arrived")
            data += chunk
        shared: socket.socket = socket.fromshare(data)
        return shared
    if fd is None:
        raise OSError("--fd is required when supervised")
    return socket.socket(fileno=fd)


def _read_line(fd: int) -> str:
    """One line from a raw fd. os.read, not sys.stdin: the buffered reader
    aborted the frozen child (SIGABRT) in the desktop spike."""
    out = bytearray()
    while True:
        byte = os.read(fd, 1)
        if not byte:
            if out:
                break
            raise EOFError
        if byte == b"\n":
            break
        out += byte
    return out.decode("ascii", "replace").strip()


class Control:
    """Reads the supervisor's words from stdin on a daemon thread."""

    def __init__(self, server: Any) -> None:
        self.server = server
        self.restart = False
        self.quit = False

    def start(self) -> None:
        threading.Thread(target=self._run, name="gt7-control", daemon=True).start()

    def _run(self) -> None:
        while True:
            try:
                word = _read_line(0)
            except (EOFError, OSError):
                word = "parent-gone"
            if word == "restart":
                self.restart = True
            if word in ("stop", "restart", "parent-gone"):
                self.server.should_exit = True
                return


def _hold_power_assertion() -> Any:
    """Keep App Nap and idle sleep from stretching the 1.6 s heartbeat.

    Best effort: PyObjC on macOS and ctypes on Windows when present; on Linux
    the supervisor (or the systemd unit) is the right place for an inhibitor.
    """
    if sys.platform == "darwin":
        try:
            from Foundation import NSProcessInfo
        except ImportError:
            return None
        # NSActivityUserInitiated | NSActivityLatencyCritical
        options = 0x00FFFFFF | 0xFF00000000
        return NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            options, "Receiving GT7 telemetry"
        )
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            es_continuous | es_system_required
        )
        return True
    return None


def _quiet_probes(record: logging.LogRecord) -> bool:
    """The supervisor asks /api/status every two seconds; 43,000 access-log
    lines a day of that would bury everything else in the log file."""
    args = record.args if isinstance(record.args, tuple) else ()
    return not (
        len(args) >= 3
        and str(args[0]).startswith("127.0.0.1")
        and str(args[2]) in ("/api/status", "/api/health")
    )


_STOP_SIGNALS = tuple(
    s for s in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)) if s is not None
)


def _make_server_class() -> Any:
    import uvicorn

    class Server(uvicorn.Server):
        """uvicorn's server without the signal re-raise at the end.

        uvicorn re-raises a SIGTERM it handled once it has shut down, so a
        clean stop still ends the process "killed by signal": -15 to a
        parent, 143 to a shell, and 127 from an AppImage's runtime. A stop
        asked for is a stop done, so this exits 0.
        """

        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            if threading.current_thread() is not threading.main_thread():
                yield
                return
            previous = {sig: signal.signal(sig, self.handle_exit) for sig in _STOP_SIGNALS}
            try:
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)

    return Server


def _watch_quit_file(path: Path, server: Any) -> None:
    path.unlink(missing_ok=True)

    def watch() -> None:
        while not server.should_exit:
            if path.exists():
                path.unlink(missing_ok=True)
                server.should_exit = True
                return
            threading.Event().wait(0.5)

    threading.Thread(target=watch, name="gt7-quit-file", daemon=True).start()


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gt7-datalogger --server")
    parser.add_argument("--supervised", action="store_true")
    parser.add_argument("--fd", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    # Headless on Windows: `--quit` asks by creating this file, since there
    # is no SIGTERM to send and taskkill without /F only closes windows.
    parser.add_argument("--quit-file", type=Path, default=None)
    return parser.parse_args(argv)


def run(argv: list[str]) -> int:
    global QUIT
    args = parse(argv)
    _unblock_signals()
    assertion = _hold_power_assertion()  # noqa: F841 - held for the process's life

    import uvicorn

    from app import discovery
    from app.config import Settings
    from app.logging_config import LOG_CONFIG, configure_logging

    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    try:
        with server_lock(settings.data_root()):
            if args.supervised:
                sock = _socket_from_parent(args.fd)
            else:
                host = args.host or settings.http_host
                port = args.port if args.port is not None else settings.http_port
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if os.name != "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
                sock.listen(128)
            host, port = sock.getsockname()[:2]
            discovery.BOUND = (host, port)

            from app.main import app

            config = uvicorn.Config(
                app,
                loop="asyncio",
                http="h11",
                ws="websockets-sansio",
                lifespan="on",
                # The LAN policy trusts the socket's peer address; a trusted
                # X-Forwarded-For from 127.0.0.1 would let anything fake it.
                proxy_headers=False,
                server_header=False,
                timeout_graceful_shutdown=1,
                log_config=LOG_CONFIG,
            )
            server = _make_server_class()(config)
            logging.getLogger("uvicorn.access").addFilter(_quiet_probes)
            control = Control(server)
            if args.supervised:
                control.start()
            if args.quit_file is not None:
                _watch_quit_file(args.quit_file, server)

            def quit_app() -> None:
                control.quit = True
                server.should_exit = True

            QUIT = quit_app
            server.run(sockets=[sock])
            if not server.started:
                return EXIT_STARTUP_FAILED
            if control.quit and args.supervised:
                return EXIT_QUIT
            return EXIT_RESTART if control.restart else EXIT_OK
    except ServerLockHeld as exc:
        logging.getLogger("app").error(
            "another GT7 Datalogger server is already using %s; not starting a second", exc
        )
        return EXIT_STARTUP_FAILED
    except OSError as exc:
        logging.getLogger("app").error("server could not start: %s", exc)
        return EXIT_STARTUP_FAILED


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
