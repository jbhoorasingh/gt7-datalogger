"""The desktop app's command line and its default (tray) mode (#192).

    gt7-datalogger                 the app: tray, supervisor, server child
    gt7-datalogger --headless      the server alone, for systemd and packages
    gt7-datalogger --server        (internal) the child the supervisor starts
    gt7-datalogger --quit          stop the running app (SIGTERM / WM_CLOSE)
    gt7-datalogger --status        print the running app's lock record as JSON
    gt7-datalogger --self-test     check the bundle and print JSON
    gt7-datalogger --firewall install|remove|repair   (Windows, elevated)

Exit codes: 0 ok, 1 a command failed, 2 usage, 3 could not start.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from gt7_desktop import APP_NAME, paths, prefs
from gt7_desktop.lock import AlreadyRunning, InstanceLock, read_record

log = logging.getLogger("gt7_desktop")

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_STARTUP = 0, 1, 2, 3
SETUP_KEY = "setup_completed"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gt7-datalogger", description=APP_NAME)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="run the server with no tray")
    mode.add_argument("--quit", action="store_true", help="stop the running app")
    mode.add_argument("--status", action="store_true", help="print the running app's state")
    mode.add_argument("--self-test", action="store_true", help="check this build")
    mode.add_argument("--firewall", choices=("install", "remove", "repair"))
    parser.add_argument("--open", action="store_true", help="open the dashboard once running")
    parser.add_argument("--background", action="store_true", help="never open the browser")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None, help="headless: bind address (default 0.0.0.0)")
    parser.add_argument("--ready-file", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=20.0, help="--quit: seconds to wait")
    return parser.parse_args(argv)


def setup_completed(db_path: Path) -> bool:
    """Read the marker straight from SQLite, read-only, before any server runs."""
    if not db_path.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (SETUP_KEY,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0])


def install_defaults_path() -> Path | None:
    """`install-defaults.json`, which the Windows installer writes next to
    the exe from its tasks (#199). None elsewhere: only Windows has one."""
    if os.name != "nt":
        return None
    return Path(sys.executable).resolve().parent / "install-defaults.json"


def read_install_defaults(path: Path | None) -> dict[str, Any]:
    """The installer's choices; {} when there are none or the file is unreadable."""
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def server_env(folders: paths.Folders, gui: bool) -> dict[str, str]:
    """The child's environment: the launcher owns where everything lives."""
    env = dict(os.environ)
    db = folders.data / "gt7.db"
    env.update(
        GT7_DATA_DIR=str(folders.data),
        GT7_DB_PATH=str(db),
        GT7_LOG_FILE=str(folders.log_file),
        GT7_DESKTOP="1",
    )
    # The installer's "Check for updates daily" task is the baseline for the
    # update check (#196); an explicit environment variable still wins.
    update_check = read_install_defaults(install_defaults_path()).get("update_check")
    env.setdefault("GT7_UPDATE_CHECK", "false" if update_check is False else "true")
    dist = paths.frontend_dist()
    if dist is not None:
        env["GT7_FRONTEND_DIST"] = str(dist)
    if gui and not setup_completed(db):
        env["GT7_FORCE_SIM"] = "firstrun"
    else:
        env.pop("GT7_FORCE_SIM", None)
    return env


def _configure_logging(folders: paths.Folders) -> None:
    from logging.handlers import RotatingFileHandler

    handler = RotatingFileHandler(
        folders.logs / "launcher.log", maxBytes=1 << 20, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr is not None:
        root.addHandler(logging.StreamHandler())


def _redirect_missing_streams(folders: paths.Folders) -> None:
    """A windowed build has no stdout or stderr; anything printing would crash."""
    if sys.stdout is None or sys.stderr is None:
        stream = open(folders.logs / "launcher.out", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream


def main(argv: list[str]) -> int:
    if argv[:1] == ["--server"]:
        from app.serve import run

        return run(argv[1:])
    try:
        args = parse(argv)
    except SystemExit as exc:
        return int(exc.code or 0) and EXIT_USAGE
    folders = paths.folders(args.data_dir)
    paths.create(folders)
    _redirect_missing_streams(folders)
    import faulthandler

    with open(folders.logs / "crash.log", "a", encoding="utf-8") as crash:  # noqa: SIM115
        faulthandler.enable(crash)
        return _dispatch(args, folders)


def _dispatch(args: argparse.Namespace, folders: paths.Folders) -> int:
    if args.status:
        print(json.dumps(read_record(folders.lock), indent=2))
        return EXIT_OK
    if args.quit:
        return quit_running(folders, args.timeout)
    if args.self_test:
        from gt7_desktop.selftest import run as self_test

        return self_test()
    if args.firewall:
        from gt7_desktop import firewall

        return firewall.run(args.firewall)
    _configure_logging(folders)
    if args.headless:
        return run_headless(args, folders)
    return run_app(args, folders)


# --- headless -----------------------------------------------------------------


def run_headless(args: argparse.Namespace, folders: paths.Folders) -> int:
    """The server alone, serving the LAN the way Docker does."""
    lock = InstanceLock(folders.lock)
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        log.error("already running: %s", exc.record.get("url", "?"))
        return EXIT_STARTUP
    host = args.host or "0.0.0.0"
    port = args.port or prefs.load(folders.prefs).port
    os.environ.update(server_env(folders, gui=False))
    lock.write(pid=os.getpid(), mode="headless", host=host, port=port,
               url=f"http://127.0.0.1:{port}/", exe=sys.executable,
               state="running", started_at=time.time())
    if args.ready_file is not None:
        threading.Thread(
            target=_write_ready_file, args=(port, args.ready_file), daemon=True
        ).start()
    from app.serve import run

    try:
        return run(["--host", host, "--port", str(port), "--quit-file", str(folders.quit_request)])
    finally:
        lock.release()


def _write_ready_file(port: int, path: Path) -> None:
    from gt7_desktop.ports import health

    for _ in range(1200):
        if health(port, 0.5) is not None:
            path.write_text(json.dumps({"port": port, "url": f"http://127.0.0.1:{port}/"}))
            return
        time.sleep(0.1)


# --- quit -----------------------------------------------------------------------


def quit_running(folders: paths.Folders, timeout: float) -> int:
    record = read_record(folders.lock)
    pid = record.get("pid")
    if not isinstance(pid, int) or record.get("state") == "stopped":
        return EXIT_OK
    try:
        if os.name == "nt":
            # No SIGTERM on Windows, and taskkill without /F only asks a
            # process's windows to close — the tray's hidden one included,
            # which is not a request to quit. The running app watches for
            # this file instead.
            folders.quit_request.touch()
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return EXIT_OK
    lock = InstanceLock(folders.lock)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lock.acquire()
        except AlreadyRunning:
            time.sleep(0.2)
            continue
        lock.release()
        return EXIT_OK
    print(f"{APP_NAME} did not stop within {timeout:.0f} s", file=sys.stderr)
    return EXIT_FAILED


# --- the app ----------------------------------------------------------------------


def open_browser(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - nothing to do but log
        log.exception("could not open the browser at %s", url)


class DesktopApp:
    """What the tray drives: the supervisor, the lock and a few OS chores."""

    def __init__(self, args: argparse.Namespace, folders: paths.Folders) -> None:
        self.args = args
        self.folders = folders
        self.prefs = prefs.load(folders.prefs)
        self.lock = InstanceLock(folders.lock)
        self.supervisor: Any = None
        self.tray: Any = None
        self._quit_once = threading.Lock()

    @property
    def url(self) -> str:
        return self.supervisor.url if self.supervisor is not None else ""

    def status_line(self) -> str:
        if self.supervisor is None:
            return "Starting…"
        state = self.supervisor.state
        if state.phase in ("starting", "restarting"):
            return "Starting…"
        if state.phase == "failed":
            return "Could not start"
        status = state.status
        if status.get("listener_error"):
            return "UDP 33740 in use"
        heartbeat = status.get("heartbeat_error") or {}
        if "Local Network" in str(heartbeat.get("reason", "")):
            return "Local Network blocked"
        if status.get("source") == "sim":
            return "Simulator"
        if status.get("connected") and status.get("recording") and status.get("session_id"):
            return "Recording"
        if status.get("connected"):
            return "PlayStation connected"
        return "Waiting for the PlayStation"

    def recording(self) -> bool:
        status = self.supervisor.state.status if self.supervisor is not None else {}
        return bool(
            status.get("connected") and status.get("recording") and status.get("session_id")
        )

    def open_dashboard(self, path: str = "") -> None:
        if self.url:
            open_browser(self.url + path.lstrip("/"))

    def open_folder(self, which: str) -> None:
        target = self.folders.data if which == "data" else self.folders.logs
        if sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(target)])
        elif os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(target)])

    def set_lan_access(self, on: bool) -> bool:
        """Flip the server's live LAN policy through its own API (#195)."""
        import urllib.request

        body = json.dumps({"values": {"lan_access": on}}).encode()
        request = urllib.request.Request(
            self.url + "api/admin/config", data=body, method="PUT",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5):  # noqa: S310
                return True
        except OSError:
            log.exception("could not change LAN access")
            return False

    def start(self) -> int:
        """Lock, port, supervisor. Returns an exit code when it cannot start."""
        try:
            self.lock.acquire()
        except AlreadyRunning as exc:
            url = exc.record.get("url")
            if url and not self.args.background:
                open_browser(url)
            return EXIT_OK
        from gt7_desktop import ports
        from gt7_desktop.supervisor import Supervisor

        loopback = self.prefs.strict_loopback or (os.name == "nt" and not _firewall_rules_present())
        host = "127.0.0.1" if loopback else "0.0.0.0"
        explicit = self.args.port is not None
        try:
            sock, port = ports.choose(host, self.args.port or self.prefs.port, explicit)
        except ports.AlreadyServing as exc:
            self._show_error(
                f"GT7 Datalogger is already running on port {exc.port} "
                f"({exc.health.get('install_kind', 'another copy')}). "
                "Stop it first, or use that one.",
            )
            open_browser(f"http://127.0.0.1:{exc.port}/")
            return EXIT_STARTUP
        except ports.NoPort as exc:
            self._show_error(f"No free port for the dashboard: {exc}.")
            return EXIT_STARTUP
        if port != self.prefs.port and not explicit:
            self.prefs.port = port
            prefs.save(self.folders.prefs, self.prefs)
        env = server_env(self.folders, gui=True)
        self.supervisor = Supervisor(sock, env, self.folders.log_file, on_change=self._changed)
        self.lock.write(pid=os.getpid(), mode="tray", host=host, port=port,
                        url=self.supervisor.url, exe=sys.executable,
                        state="starting", started_at=time.time())
        self.supervisor.start()
        threading.Thread(target=self._after_ready, name="gt7-ready", daemon=True).start()
        threading.Thread(target=self._watch_quit_request, name="gt7-quit-file",
                         daemon=True).start()
        return EXIT_OK

    def _after_ready(self) -> None:
        self.supervisor.ready.wait()
        if self.supervisor.state.phase != "running":
            return
        self.lock.write(state="running")
        if self.args.ready_file is not None:
            self.args.ready_file.write_text(json.dumps({"port": self.supervisor.port,
                                                        "url": self.url}))
        first = not self.prefs.opened_once
        if not self.args.background and (self.args.open or first or not _launched_at_login()):
            self.open_dashboard()
        if first:
            self.prefs.opened_once = True
            prefs.save(self.folders.prefs, self.prefs)

    def _watch_quit_request(self) -> None:
        """`--quit` on Windows creates the request file; quit as the tray would."""
        path = self.folders.quit_request
        path.unlink(missing_ok=True)
        while not self._quit_once.locked():
            if path.exists():
                path.unlink(missing_ok=True)
                log.info("quit requested")
                if self.tray is not None:
                    self.tray.request_quit()
                else:
                    self.quit()
                return
            time.sleep(0.5)

    def _changed(self, state: Any) -> None:
        if state.phase == "failed":
            self.lock.write(state="failed")
            self._show_error(f"{state.error}\n\n{state.log_tail[-1500:]}")
        if self.tray is not None:
            self.tray.refresh()

    def _show_error(self, message: str) -> None:
        log.error("%s", message)
        if self.tray is not None:
            self.tray.alert(APP_NAME, message)

    def quit(self) -> None:
        """Stop the server, then the process. Safe to call from any thread."""
        if not self._quit_once.acquire(blocking=False):
            return
        if self.supervisor is not None:
            self.supervisor.stop()
        self.lock.release()


def _launched_at_login() -> bool:
    return "--login" in sys.argv


def _firewall_rules_present() -> bool:
    from gt7_desktop import firewall

    return firewall.rules_present()


_SIGNALS = {signal.SIGTERM, signal.SIGINT, getattr(signal, "SIGHUP", signal.SIGTERM)}


def _block_signals() -> None:
    """Before any thread exists, so every thread inherits the block and
    only the sigwait thread ever takes SIGTERM — a toolkit thread taking it
    with the default action killed the app instead of quitting it."""
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_BLOCK, _SIGNALS)


def _watch_signals(app: DesktopApp) -> None:
    """SIGTERM (`--quit`, logout, a package script) ends the app cleanly."""
    if not hasattr(signal, "sigwait"):
        return
    wanted = _SIGNALS

    def wait() -> None:
        signal.sigwait(wanted)
        log.info("signal received; quitting")
        if app.tray is not None:
            app.tray.request_quit()
        else:
            app.quit()

    threading.Thread(target=wait, name="gt7-signals", daemon=True).start()


def run_app(args: argparse.Namespace, folders: paths.Folders) -> int:
    _block_signals()
    from gt7_desktop.tray import make_tray

    app = DesktopApp(args, folders)
    app.tray = make_tray(app)
    _watch_signals(app)
    # The tray first, so "Starting…" shows before anything slow happens.
    code = app.start()
    if app.supervisor is None:
        return code
    try:
        app.tray.run()
    finally:
        app.quit()
    return EXIT_OK
