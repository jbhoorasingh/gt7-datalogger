#!/usr/bin/env python3
"""Build the desktop app for this OS (#193). Same steps on all three:

    python desktop/scripts/build.py [--install] [--skip-frontend] [--no-smoke]

1. build the frontend if frontend/dist is missing;
2. with --install, install this platform's hashed lock from desktop/locks/
   (the backend's dependencies with its `desktop` extra, and the build
   tools), then the backend itself without dependencies, into the running
   interpreter (CI runs it in a fresh venv; locally, use one too);
3. run PyInstaller on desktop/gt7.spec into desktop/dist/;
4. run the frozen binary's --self-test.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESKTOP = ROOT / "desktop"
DIST = DESKTOP / "dist"
NAME = "gt7-datalogger"


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


# Build target -> the lock desktop/locks/lock.sh writes for it.
LOCKS = {
    ("darwin", "arm64"): "aarch64-apple-darwin",
    ("win32", "AMD64"): "x86_64-pc-windows-msvc",
    ("linux", "x86_64"): "x86_64-manylinux_2_28",
}


def lock_file() -> Path | None:
    import platform

    name = LOCKS.get((sys.platform, platform.machine()))
    return DESKTOP / "locks" / f"{name}.txt" if name else None


def install() -> None:
    pip = [sys.executable, "-m", "pip"]
    run([*pip, "install", "--upgrade", "pip"])
    lock = lock_file()
    if lock is None or not lock.exists():
        # An Intel Mac or an ARM Linux box: no reviewed lock, so resolve.
        print(f"build: no lock for {sys.platform}; installing unpinned", file=sys.stderr)
        run([*pip, "install", f"{ROOT / 'backend'}[desktop]",
             "-r", str(DESKTOP / "requirements-build.txt")])
        return
    run([*pip, "install", "--require-hashes", "-r", str(lock)])
    run([*pip, "install", "--no-deps", "--no-build-isolation", str(ROOT / "backend")])


def frozen_executable() -> Path:
    if sys.platform == "darwin":
        return DIST / "GT7 Datalogger.app" / "Contents" / "MacOS" / NAME
    if os.name == "nt":
        return DIST / NAME / f"{NAME}.exe"
    return DIST / NAME / NAME


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument("--no-smoke", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_frontend and not (ROOT / "frontend" / "dist" / "index.html").exists():
        npm = shutil.which("npm") or "npm"
        run([npm, "ci", "--no-fund", "--no-audit"], cwd=ROOT / "frontend")
        run([npm, "run", "build"], cwd=ROOT / "frontend")
    if args.install:
        install()
    shutil.rmtree(DIST, ignore_errors=True)
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(DESKTOP / "build"),
         str(DESKTOP / "gt7.spec")])
    exe = frozen_executable()
    if not exe.exists():
        print(f"build: {exe} was not produced", file=sys.stderr)
        return 1
    if args.no_smoke:
        return 0
    result = subprocess.run([str(exe), "--self-test"], capture_output=True, text=True,
                            timeout=120, check=False)
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return 1
    print(json.dumps({"built": str(exe)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
