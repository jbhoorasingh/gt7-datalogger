"""Whole-file writes that a crash, or Windows, cannot leave half done (#187).

Every durable file beside the database — the bundles, their corrections and
compiles, the car widths and inventory, the sync state, the secrets — is
written to a temp file in the same directory and then moved over the old
one, so a reader sees the old file or the new one and never part of either.

On POSIX the move cannot fail over an open target. On Windows it can:
`os.replace` raises PermissionError while anything holds the target open,
and Defender, the Search indexer and OneDrive all open new files for a
moment to look at them. That is a wait, not a refusal, so on Windows the
move is retried a few times, a little longer each time, before the error
is let through. Everywhere else the first PermissionError is the answer.

A survey log is claimed instead of replaced: it must never land over an
existing file, which `claim` keeps true where a hard link is not to be had.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Seconds before each further try, 0.575 s in all. Long enough for a scan of
# a JSON file to let go; short enough that a writer on the event loop is not
# noticed waiting. Only ever slept on Windows.
RETRY_DELAYS = (0.05, 0.075, 0.1, 0.15, 0.2)
# Without it Windows opens a descriptor in text mode and doubles every \n.
_BINARY = getattr(os, "O_BINARY", 0)


def _windows() -> bool:
    return os.name == "nt"


def replace(source: Path, target: Path) -> None:
    """`os.replace`, waited out on Windows while something holds `target`."""
    delays = RETRY_DELAYS if _windows() else ()
    for delay in delays:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            time.sleep(delay)
    try:
        os.replace(source, target)
    except PermissionError:
        if delays:
            log.warning("%s still held open after %d tries", target, len(delays) + 1)
        raise


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o666) -> None:
    """Replace `path` with `data` whole: written, flushed and synced under a
    temp name beside it, then moved into place.

    The temp name is `<name>.tmp`, one per file, as it always was: writers of
    one file are serialised by their callers. A leftover from a crashed write
    is removed first rather than reused, so `mode` (less the umask) is what
    the new file gets — `0o600` keeps a secret owner-only from its first byte.
    """
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, mode)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path, text: str, *, encoding: str = "utf-8", mode: int = 0o666
) -> None:
    """`atomic_write_bytes` for text. Newlines are written as given, `\\n`
    on every platform, so a file reads back the same wherever it was made."""
    atomic_write_bytes(path, text.encode(encoding), mode=mode)


def claim(source: Path, target: Path) -> bool:
    """Put a copy of `source` at `target` unless something is there already;
    whether it was put. `source` stays, for the caller to remove.

    A hard link is tried first: atomic, and the file appears whole. Where the
    filesystem has none — FAT32, exFAT, many SMB shares — the name is taken
    with an exclusive create (O_CREAT | O_EXCL), which fails as the link does
    if the name exists, and the bytes are copied in after. Either way two
    claims of one name cannot both win, and nothing is ever overwritten.
    """
    try:
        os.link(source, target)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        log.debug("no hard link for %s (%s): copying", target, exc)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o666)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        # The name is ours, so what is there is the half copy: not a file to keep.
        target.unlink(missing_ok=True)
        raise
    return True
