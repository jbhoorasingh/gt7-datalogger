#!/usr/bin/env python3
"""Update an installed desktop app in place and check that the new one runs.

    python desktop/ci/update_smoke.py macos "<App>.app" <signed .dmg> [--version X]
    python desktop/ci/update_smoke.py windows <installed exe> <Setup.exe> [--signed] [--version X]
    python desktop/ci/update_smoke.py appimage <AppImage> [--version X]

Each starts the installed app as a user would (the default data folder, in
the background), runs the same install step Restart and update runs
(gt7_desktop.selfupdate), quits the app, and waits for the new copy to come
back by itself. The "new" release is the same build: what is tested is the
install step, which the version checks before it (tests/test_self_update.py)
never reach in CI.

- macos: the signed DMG is mounted and copied out, checked against the
  installed app's designated requirement and Gatekeeper, and swapped in by
  the helper, which opens it.
- windows: Setup runs with Restart and update's switches over the running
  app, stops it with --quit and starts it again (/RELAUNCH=1). With --signed,
  the installed exe and Setup must carry the same Authenticode signer.
- appimage: a copy of the AppImage is replaced in place and started again.

Stdlib only: it runs in jobs that install nothing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gt7_desktop import selfupdate  # noqa: E402
from gt7_desktop.selfupdate import Install  # noqa: E402


class SmokeFailed(Exception):
    pass


def check(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        raise SmokeFailed(what)


def status(exe: Path) -> dict[str, Any]:
    out = subprocess.run([str(exe), "--status"], capture_output=True, text=True, timeout=120,
                         check=False)
    try:
        record = json.loads(out.stdout or "{}")
    except ValueError:
        return {}
    return record if isinstance(record, dict) else {}


def health(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url + "api/health", timeout=3) as r:
            body = json.loads(r.read())
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def wait_running(exe: Path, seconds: float, not_pid: int | None = None) -> dict[str, Any]:
    """The lock record of a running app (another one than not_pid), once it serves."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        record = status(exe)
        pid, url = record.get("pid"), record.get("url")
        if (record.get("state") == "running" and isinstance(url, str) and pid != not_pid
                and health(url) is not None):
            return record
        time.sleep(1)
    raise SmokeFailed(f"no running app after {seconds:.0f} s (last record: {status(exe)})")


def quit_app(exe: Path) -> None:
    subprocess.run([str(exe), "--quit", "--timeout", "60"], timeout=120, check=False)


def check_version(record: dict[str, Any], expected: str | None) -> None:
    found = health(str(record["url"])) or {}
    if expected:
        reported = str(found.get("version", "")).split("+", 1)[0]
        check(reported == expected, f"the new copy reports {found.get('version')}")


def smoke_macos(app: Path, dmg: Path, expected: str | None, work: Path) -> None:
    exe = app / "Contents" / "MacOS" / "gt7-datalogger"
    requirement = selfupdate.designated_requirement(app)
    check("subject.OU" in requirement, "the installed app has a Developer ID requirement")
    subprocess.Popen([str(exe), "--background"])
    first = wait_running(exe, 120)
    staged = work / "updates" / "v-smoke"
    staged.mkdir(parents=True)
    shutil.copyfile(dmg, staged / dmg.name)
    failed = work / "failed.json"
    log_file = work / "update.log"
    try:
        selfupdate.install_macos(Install("macos-app", app, requirement), staged / dmg.name,
                                 "v-smoke", failed, log_file, relaunch=True, pid=first["pid"])
        check(True, "the new app passed the codesign and Gatekeeper checks")
        quit_app(exe)
        second = wait_running(exe, 180, not_pid=first["pid"])
        check(True, f"the swapped-in app started by itself (pid {second['pid']})")
        check(not failed.exists(), "the swap reported no failure")
        check(not (staged / "replaced.app").exists(), "the old bundle is gone")
        check_version(second, expected)
        for cmd in (["codesign", "--verify", "--deep", "--strict", str(app)],
                    ["spctl", "--assess", "--type", "execute", str(app)]):
            check(subprocess.run(cmd, check=False).returncode == 0, " ".join(cmd[:2]))
    finally:
        if log_file.exists():
            print("--- update.log ---\n" + log_file.read_text())
        quit_app(exe)


def smoke_windows(exe: Path, setup: Path, signed: bool, expected: str | None) -> None:
    running, offered = selfupdate.authenticode(exe), selfupdate.authenticode(setup)
    print(f"installed exe: {running}; Setup: {offered}")
    if signed:
        selfupdate.check_signer(running, offered)
        check(running[0] == "Valid", "the installed exe and Setup share their signer")
    else:
        check(running[0] != "Valid", "an unsigned build is not taken for a signed one")
    subprocess.Popen([str(exe), "--background"])
    first = wait_running(exe, 120)
    try:
        result = subprocess.run([str(setup), *selfupdate.RELAUNCH_ARGS.split()], timeout=900,
                                check=False)
        check(result.returncode == 0, f"Setup over the running app exits 0 ({result.returncode})")
        second = wait_running(exe, 180, not_pid=first["pid"])
        check(True, f"Setup started the app again (pid {second['pid']})")
        check_version(second, expected)
    finally:
        quit_app(exe)


def smoke_appimage(image: Path, expected: str | None, work: Path) -> None:
    installed = work / "Applications" / image.name
    installed.parent.mkdir(parents=True)
    shutil.copyfile(image, installed)
    installed.chmod(installed.stat().st_mode | stat.S_IXUSR)
    subprocess.Popen([str(installed), "--background"])
    first = wait_running(installed, 120)
    log_file = work / "update.log"
    try:
        selfupdate.install_appimage(Install("appimage", installed), image, log_file,
                                    relaunch=True, pid=first["pid"])
        check(installed.read_bytes() == image.read_bytes(), "the AppImage was replaced")
        quit_app(installed)
        second = wait_running(installed, 180, not_pid=first["pid"])
        check(True, f"the new AppImage started by itself (pid {second['pid']})")
        check_version(second, expected)
    finally:
        if log_file.exists():
            print("--- update.log ---\n" + log_file.read_text())
        quit_app(installed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("macos", "windows", "appimage"))
    parser.add_argument("installed", type=Path)
    parser.add_argument("new", type=Path, nargs="?")
    parser.add_argument("--signed", action="store_true")
    parser.add_argument("--version", default=None)
    args = parser.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="gt7-update-smoke-"))
    try:
        if args.kind == "macos":
            smoke_macos(args.installed, args.new, args.version, work)
        elif args.kind == "windows":
            smoke_windows(args.installed, args.new, args.signed, args.version)
        else:
            smoke_appimage(args.installed, args.version, work)
    except (SmokeFailed, selfupdate.InstallFailed) as exc:
        print(f"FAIL {exc}", flush=True)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
