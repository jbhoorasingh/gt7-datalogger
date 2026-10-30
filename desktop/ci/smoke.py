#!/usr/bin/env python3
"""Smoke-test a built desktop app against an empty data folder (#197).

    python desktop/ci/smoke.py <executable> [--version X.Y.Z[-rc.N]] [--headless]

Stdlib only: it runs in jobs that install nothing. Checks, in order:
- the app starts and writes its ready file;
- /api/health has the published shape, says desktop, and the version;
- the schema is at head and / serves the SPA;
- /ws/live upgrades;
- (tray mode) the server child killed with SIGKILL is respawned and serves;
- --quit stops it with exit 0 and the port is released;
- --self-test passes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


class SmokeFailed(Exception):
    pass


def check(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SmokeFailed(what)


def get(url: str, timeout: float = 5) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        return r.status, r.read()


def wait_for(path: Path, seconds: float) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return json.loads(path.read_text())
        time.sleep(0.2)
    raise SmokeFailed(f"no ready file after {seconds:.0f} s")


def websocket_upgrades(port: int) -> bool:
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(
            (f"GET /ws/live HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
             f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
             "Sec-WebSocket-Version: 13\r\n\r\n").encode()
        )
        return s.recv(1024).startswith(b"HTTP/1.1 101")


def child_pids(parent: int) -> list[int]:
    if os.name == "nt":
        return []
    out = subprocess.run(["pgrep", "-P", str(parent)], capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("--version", default=None)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)
    exe = str(args.exe)
    data = Path(tempfile.mkdtemp(prefix="gt7-smoke-"))
    ready = data / "ready.json"
    mode = ["--headless", "--host", "127.0.0.1"] if args.headless else ["--background"]
    env = {**os.environ, "GT7_SHARED_BUNDLES_URL": "", "GT7_UPDATE_CHECK": "false"}
    proc = subprocess.Popen(
        [exe, *mode, "--data-dir", str(data), "--port", "0" if not args.headless else "8765",
         "--ready-file", str(ready)],
        env=env,
    )
    try:
        port = int(wait_for(ready, 180)["port"])
        status, body = get(f"http://127.0.0.1:{port}/api/health")
        health = json.loads(body)
        check(status == 200, "health answers")
        check(set(health) == {"status", "app", "version", "install_kind", "schema"},
              f"health shape {sorted(health)}")
        check(health["app"] == "gt7-datalogger" and health["install_kind"] == "desktop",
              "health says gt7-datalogger, desktop")
        if args.version:
            check(health["version"] == args.version, f"version {health['version']}")
        check(health["schema"]["current"] == health["schema"]["head"], "schema at head")
        status, body = get(f"http://127.0.0.1:{port}/")
        check(status == 200 and b"<div id=\"root\"" in body, "/ serves the SPA")
        check(websocket_upgrades(port), "/ws/live upgrades")
        if not args.headless and os.name != "nt":
            children = child_pids(proc.pid)
            check(len(children) == 1, f"one server child ({children})")
            os.kill(children[0], signal.SIGKILL)
            deadline = time.monotonic() + 30
            back = False
            while time.monotonic() < deadline:
                now = child_pids(proc.pid)
                if now and now != children:
                    try:
                        back = get(f"http://127.0.0.1:{port}/api/health", 2)[0] == 200
                    except OSError:
                        back = False
                    if back:
                        break
                time.sleep(0.2)
            check(back, "a killed server child is respawned and serves")
        quit_ = subprocess.run([exe, "--data-dir", str(data), "--quit", "--timeout", "30"],
                               timeout=60, check=False)
        check(quit_.returncode == 0, "--quit exits 0")
        proc.wait(30)
        check(proc.returncode in (0, -signal.SIGTERM), f"app exited {proc.returncode}")
        probe = socket.socket()
        check(probe.connect_ex(("127.0.0.1", port)) != 0, "port released")
        probe.close()
        test = subprocess.run([exe, "--self-test"], capture_output=True, text=True,
                              timeout=120, check=False, env=env)
        check(test.returncode == 0, "--self-test passes")
    except SmokeFailed:
        log = data / "logs" / "gt7-datalogger.log"
        if log.exists():
            print("--- server log tail ---\n" + "\n".join(log.read_text().splitlines()[-40:]))
        return 1
    finally:
        if proc.poll() is None:
            proc.kill()
    print("smoke passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
