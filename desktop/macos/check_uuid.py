#!/usr/bin/env python3
"""Fail when the app's executable has the stock PyInstaller bootloader's
LC_UUID (#198). Apple TN3179: macOS keys the Local Network grant on the main
executable's UUID, so every PyInstaller app sharing one would share — or
collide on — the user's answer.

    python desktop/macos/check_uuid.py "<App>.app/Contents/MacOS/gt7-datalogger"
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


def uuids(path: Path) -> set[str]:
    out = subprocess.run(["dwarfdump", "--uuid", str(path)], capture_output=True, text=True,
                         check=True).stdout
    return set(re.findall(r"UUID: ([0-9A-F-]{36})", out))


def main(exe: Path) -> int:
    import PyInstaller

    stock_dir = Path(PyInstaller.__file__).parent / "bootloader" / "Darwin-64bit"
    stock = set().union(*(uuids(p) for p in stock_dir.glob("run*") if p.is_file()))
    mine = uuids(exe)
    print(f"executable: {sorted(mine)}; stock bootloaders: {len(stock)} UUIDs")
    if not mine or mine & stock:
        print("LC_UUID matches PyInstaller's stock bootloader: rebuild the bootloader",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
