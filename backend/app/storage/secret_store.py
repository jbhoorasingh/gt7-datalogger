"""Credentials kept beside the database, not in it (#94).

The SQLite file holds every session and lap, and it is what people copy when
asked for their data: for a bug report, a backup on a shared drive, a survey
collection handed to someone else. Two settings are credentials — the webhook
URL (a Discord webhook lets anyone holding it post to the channel) and the
sync token (an account on the sync service) — so they are kept out of it, in
`secrets.json` in the data directory, readable by its owner only. Every other
setting Admin saves stays in the `settings` table.

A value here overlays the environment exactly as a saved row does: an empty
string is a cleared setting, which outranks `GT7_SYNC_TOKEN`; a missing key
means the environment's value stands.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.storage.atomic import atomic_write_text

if TYPE_CHECKING:
    from app.storage.repository import Repository

log = logging.getLogger(__name__)

FORMAT = "gt7-datalogger-secrets"
VERSION = 1
FILENAME = "secrets.json"
# The settings kept here rather than in the database.
KEYS = ("webhook_url", "sync_token")


class SecretStore:
    """The secrets file of one data directory.

    Read from disk on every call rather than cached: it is a few bytes, read
    at startup and when Settings opens, and nothing can then go stale.
    """

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / FILENAME

    def read(self) -> dict[str, str]:
        """key -> saved value; empty when there is no file or it is unreadable."""
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("unreadable secrets file %s: %s", self.path, exc)
            return {}
        values = doc.get("settings") if isinstance(doc, dict) else None
        if not isinstance(values, dict) or doc.get("format") != FORMAT:
            log.warning("ignoring %s: not a secrets file", self.path)
            return {}
        return {k: v for k, v in values.items() if k in KEYS and isinstance(v, str)}

    def set(self, key: str, value: str) -> None:
        self.update({key: value})

    def update(self, values: dict[str, str]) -> None:
        self._write({**self.read(), **values})

    def delete(self, key: str) -> None:
        values = self.read()
        if values.pop(key, None) is not None:
            self._write(values)

    def _write(self, values: dict[str, str]) -> None:
        doc = {"format": FORMAT, "version": VERSION, "settings": dict(sorted(values.items()))}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Created owner-only rather than chmodded after, so there is no moment
        # another user could read it (atomic_write_text removes a leftover temp
        # file first, as O_CREAT would keep its permissions).
        atomic_write_text(self.path, json.dumps(doc, indent=1), mode=0o600)


async def move_out_of_database(repo: Repository, store: SecretStore) -> None:
    """Carry credentials an older version saved in the database into the file,
    then purge them from the database. Runs at every start; once done, a
    single read finds nothing to move.

    The database's value wins over one already in the file. The file is
    written before the rows go, so after a crash between the two both hold
    the same value; and a row written after the file — by an older version
    run against this data directory again — is the newer of the two.
    """
    stored = await repo.get_settings()
    found = {k: stored[k] for k in KEYS if k in stored}
    if not found:
        return
    try:
        store.update(found)
    except OSError as exc:
        # Left where they are: the values still load from the rows, and the
        # move is tried again at the next start.
        log.warning("could not move %s out of the database: %s", ", ".join(found), exc)
        return
    await repo.purge_settings(KEYS)
    log.info("moved %s out of the database into %s", ", ".join(found), store.path.name)
