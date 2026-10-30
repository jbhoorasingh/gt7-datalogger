"""One desktop app per data folder: gt7.lock and the record in it.

The lock is an OS lock on the file (flock, or msvcrt on one byte far from
offset 0 so the record stays readable on Windows), released by the OS when
the process dies however it dies. The record at offset 0 says who holds it
and where its UI is, so a second launch opens that instead of starting.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import IO, Any

_WIN_LOCK_OFFSET = 1 << 20


class AlreadyRunning(Exception):
    def __init__(self, record: dict[str, Any]) -> None:
        super().__init__(record.get("url", ""))
        self.record = record


def read_record(path: Path) -> dict[str, Any]:
    try:
        with open(path, "rb") as fh:
            raw = fh.read(64 * 1024).split(b"\0", 1)[0]
        record = json.loads(raw.decode("utf-8") or "{}")
    except (OSError, ValueError):
        return {}
    return record if isinstance(record, dict) else {}


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: IO[bytes] | None = None
        self.record: dict[str, Any] = {}

    def acquire(self) -> None:
        # r+b, not a+b: append mode sends every write to the end, and the
        # record is rewritten in place at offset 0.
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fh = os.fdopen(fd, "r+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(_WIN_LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunning(read_record(self.path)) from None
        self._fh = fh

    def write(self, **fields: Any) -> None:
        if self._fh is None:
            return
        self.record.update(fields)
        data = json.dumps(self.record).encode() + b"\0"
        self._fh.seek(0)
        self._fh.write(data)
        if os.name != "nt":  # Windows keeps its locked byte far past the record
            self._fh.truncate()
        self._fh.flush()

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self.write(state="stopped", stopped_at=time.time())
        finally:
            self._fh.close()
            self._fh = None
