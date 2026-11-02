"""Where log lines go: stderr, the in-memory buffer, and optionally a file (#180).

A crash or a failed start used to leave nothing to attach to a bug report:
lines went to stderr and to a 2,000-record ring in memory. With GT7_LOG_FILE
set they also go to a rotating file.

uvicorn's own loggers are the awkward part. Its default config sets
`propagate=False` on "uvicorn" and "uvicorn.access", so a handler on the root
logger never sees the bind failure or "Application startup failed" — the two
lines a log file is most wanted for. Whoever starts uvicorn decides its log
config: `python -m app.main` and the desktop server pass LOG_CONFIG below,
which routes uvicorn's loggers to the root; the Docker image and `npm run
dev` run the uvicorn CLI with its default, so `configure_logging` also hangs
the file handler on the two non-propagating uvicorn loggers itself.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

from app import logbuffer

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
FILE_BYTES = 5 * 1024 * 1024
FILE_BACKUPS = 4  # five files in all, 25 MB at most

# For uvicorn.run(log_config=...): no handlers of uvicorn's own, everything
# into the root logger. No colours either, which also keeps uvicorn from
# calling sys.stdout.isatty() — stdout is None in a windowed desktop build.
LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "uvicorn": {"handlers": [], "propagate": True},
        "uvicorn.error": {"handlers": [], "propagate": True},
        "uvicorn.access": {"handlers": [], "propagate": True},
    },
}

_UVICORN_ISOLATED = ("uvicorn", "uvicorn.access")


def _file_handler(path: Path) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=FILE_BYTES, backupCount=FILE_BACKUPS, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler


def _same_file(handler: logging.Handler, path: Path) -> bool:
    return isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == path.resolve()


def configure_logging(level: str, log_file: Path | None = None) -> None:
    """Set up the root logger once; safe to call again (a restore, a test).

    `force=True` because basicConfig is otherwise a no-op as soon as any
    handler exists — which silently dropped GT7_LOG_LEVEL whenever something
    had installed one first.
    """
    keep = [h for h in logging.getLogger().handlers if isinstance(h, logbuffer.RingBufferHandler)]
    logging.basicConfig(level=level, format=FORMAT, force=True)
    root = logging.getLogger()
    for kept in keep:
        root.addHandler(kept)
    logbuffer.install()
    if log_file is None or str(log_file) in ("", "."):
        return
    file_handler = _file_handler(log_file)
    root.addHandler(file_handler)
    for name in _UVICORN_ISOLATED:
        logger = logging.getLogger(name)
        if logger.propagate:
            continue  # reaches the root, and the root has the file
        # The root's previous file handler was just closed by force=True;
        # swap it here too rather than leave two writers on one file.
        for stale in [h for h in logger.handlers if _same_file(h, log_file)]:
            logger.removeHandler(stale)
        logger.addHandler(file_handler)
