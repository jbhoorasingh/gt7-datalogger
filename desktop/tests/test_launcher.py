"""The desktop launcher's pieces, and the supervisor against a real server child (#192)."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from gt7_desktop import lock, main, paths, ports, prefs
from gt7_desktop.supervisor import Supervisor, server_command


def test_macos_folders_ignore_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    f = paths.default_folders()
    assert f.data == Path.home() / "Library" / "Application Support" / "GT7 Datalogger"
    assert f.logs == Path.home() / "Library" / "Logs" / "GT7 Datalogger"


def test_data_dir_holds_the_logs_too(tmp_path: Path) -> None:
    f = paths.folders(tmp_path / "d")
    paths.create(f)
    assert f.logs == (tmp_path / "d").resolve() / "logs" and f.logs.is_dir()
    if os.name != "nt":
        assert (f.data.stat().st_mode & 0o777) == 0o700


def test_prefs_survive_garbage(tmp_path: Path) -> None:
    path = tmp_path / "desktop.json"
    path.write_text("{not json")
    assert prefs.load(path) == prefs.Prefs()
    path.write_text(json.dumps({"port": 99999, "unknown": 1}))
    assert prefs.load(path).port == prefs.DEFAULT_PORT
    prefs.save(path, prefs.Prefs(port=8003))
    assert prefs.load(path).port == 8003


def test_one_instance_per_data_folder(tmp_path: Path) -> None:
    first = lock.InstanceLock(tmp_path / "gt7.lock")
    first.acquire()
    first.write(pid=1, url="http://127.0.0.1:8000/", state="running")
    first.write(state="stopping")  # rewritten in place, not appended
    record = lock.read_record(tmp_path / "gt7.lock")
    assert record == {"pid": 1, "url": "http://127.0.0.1:8000/", "state": "stopping"}
    second = lock.InstanceLock(tmp_path / "gt7.lock")
    with pytest.raises(lock.AlreadyRunning) as raised:
        second.acquire()
    assert raised.value.record["url"] == "http://127.0.0.1:8000/"
    first.release()
    second.acquire()
    second.release()


def test_a_busy_port_falls_back_unless_asked_for() -> None:
    held, port = ports.choose("127.0.0.1", 0, explicit=True)
    try:
        with pytest.raises(ports.NoPort):
            ports.choose("127.0.0.1", port, explicit=True)
    finally:
        held.close()


def test_the_first_run_marker_is_read_from_the_database(tmp_path: Path) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    env = main.server_env(f, gui=True)
    assert env["GT7_FORCE_SIM"] == "firstrun"
    assert env["GT7_DESKTOP"] == "1"
    assert env["GT7_DB_PATH"] == str(f.data / "gt7.db")
    con = sqlite3.connect(f.data / "gt7.db")
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('setup_completed', '2026-10-30')")
    con.commit()
    con.close()
    assert "GT7_FORCE_SIM" not in main.server_env(f, gui=True)
    assert "GT7_FORCE_SIM" not in main.server_env(paths.folders(tmp_path / "x"), gui=False)


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as r:  # noqa: S310
        return json.loads(r.read())


@pytest.mark.skipif(os.name == "nt", reason="POSIX fd hand-down; Windows runs in the frozen smoke")
def test_the_supervisor_starts_respawns_and_stops_a_real_server(tmp_path: Path) -> None:
    f = paths.folders(tmp_path)
    paths.create(f)
    quiet = {"GT7_SHARED_BUNDLES_URL": "", "GT7_UPDATE_CHECK": "false"}
    env = {**main.server_env(f, gui=True), **quiet}
    sock, port = ports.choose("127.0.0.1", 0, explicit=True)
    sup = Supervisor(sock, env, f.log_file, command=server_command())
    sup.start()
    try:
        assert sup.ready.wait(60)
        assert sup.state.phase == "running"
        health = _get(f"http://127.0.0.1:{port}/api/health")
        assert health["install_kind"] == "desktop"
        first = sup._proc
        assert first is not None
        os.kill(first.pid, signal.SIGKILL)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if sup._proc is not first and sup.state.phase == "running":
                break
            time.sleep(0.1)
        assert sup._proc is not first and sup.state.phase == "running"
        assert _get(f"http://127.0.0.1:{port}/api/health")["app"] == "gt7-datalogger"
    finally:
        sup.stop()
        sock.close()
    assert sup._proc is not None and sup._proc.returncode == 0
    probe = socket.socket()
    assert probe.connect_ex(("127.0.0.1", port)) != 0
    probe.close()


@pytest.mark.skipif(os.name == "nt", reason="flock semantics")
def test_a_second_server_on_the_same_data_is_refused(tmp_path: Path) -> None:
    from app import serve

    with serve.server_lock(tmp_path), pytest.raises(serve.ServerLockHeld):
        with serve.server_lock(tmp_path):
            pass
