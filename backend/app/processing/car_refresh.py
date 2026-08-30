"""Keeping the car inventory current — the one implementation of "update cars".

Three callers, one code path (#57): the startup staleness check, the admin
endpoint, and `scripts/update_cars.py`. Before this, the endpoint and the
script each had their own copy of a download-and-map against a third-party
mirror, and nothing refreshed on its own.

The layering this sits in the middle of:

  1. The inventory shipped inside the package. Always present, never written
     to — a pip install's site-packages is not ours to modify, and on a
     read-only container it is not writable anyway.
  2. This refresh, which writes its result next to the database instead, where
     the user's own data already lives, and which that install then prefers.
  3. The manual paths, for someone who does not want to wait for the interval.

Nothing here may fail a start. Every failure mode ends the same way: the
inventory already loaded from layer 1 stays exactly as it was.
"""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.processing import car_source
from app.processing.cars import SCHEMA_VERSION, CarDatabase, Inventory, dumps

if TYPE_CHECKING:  # a type annotation only: processing does not import storage
    from app.storage.repository import Repository

log = logging.getLogger(__name__)

# Settings-table keys. Same persisted key-value pattern as the rest of startup.
UPDATED_AT_KEY = "cars_updated_at"        # ISO date of the last successful refresh
VERSION_KEY = "car_data_version"          # inventory schema the DB was last filled from
GENERATED_KEY = "car_data_generated"      # which inventory AND column shape the rows hold

# The shape of the denormalised car columns on the session rows. Bumped by any
# release that adds or changes one — and that bump is what makes the backfill
# re-run for rows written before it. The inventory file can be byte-identical
# across such an upgrade (0009 added nine columns without touching it), so
# neither its version nor its date can detect the change on their own.
COLUMNS_VERSION = 2


def stamp(cars: CarDatabase) -> str:
    """The marker recording what the session rows were last filled from."""
    return f"{COLUMNS_VERSION}:{cars.schema_version}:{cars.generated}"

# How long a refreshed inventory is considered current. GT7 content updates
# land monthly at their fastest, so a week is already generous; the point is
# that an install left running for months does not go a year without checking.
STALE_AFTER = datetime.timedelta(days=7)


def is_stale(
    stored: dict[str, str],
    today: datetime.date,
    *,
    stale_after: datetime.timedelta = STALE_AFTER,
) -> bool:
    """Whether a refresh is due.

    Due on a first run (nothing recorded), on a schema bump (the stored data
    was built by a release that knew fewer fields), and once the interval has
    elapsed. An unparseable or future-dated marker counts as due: a clock that
    has moved backwards should cost one fetch, not permanent staleness.
    """
    if stored.get(VERSION_KEY) != str(SCHEMA_VERSION):
        return True
    try:
        last = datetime.date.fromisoformat(stored[UPDATED_AT_KEY])
    except (KeyError, ValueError):
        return True
    return not (today - stale_after < last <= today)


async def fetch_and_store(
    cars: CarDatabase,
    destination: Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> Inventory:
    """Fetch the current car list, merge it over what is loaded, and save it.

    Merged rather than replaced so a car GT7 has stopped publishing keeps its
    name — someone's recorded sessions still refer to it. Raises on any network
    or parse failure; callers decide whether that is fatal (it never is).
    """
    if client is None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as owned:
            fresh = await car_source.fetch(owned)
    else:
        fresh = await car_source.fetch(client)

    merged = car_source.merge(cars.inventory(), fresh)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Written whole, then moved into place: a half-written inventory that a
    # later start would read is worse than no refresh at all.
    tmp = destination.with_suffix(destination.suffix + ".tmp")
    tmp.write_text(dumps(merged) + "\n", encoding="utf-8")
    tmp.replace(destination)
    cars.replace(merged)
    log.info(
        "car inventory refreshed: %d cars (%d from this fetch) -> %s",
        len(merged.cars), len(fresh.cars), destination,
    )
    return merged


async def record(repo: Repository, cars: CarDatabase, today: datetime.date) -> int:
    """Persist what was refreshed, and re-derive the session rows from it.

    The bookkeeping every successful refresh owes, in one place because both
    the background check and the admin button owe exactly the same: the two
    markers `is_stale` reads next time, and the denormalised car columns on
    sessions recorded before this inventory could describe their car.

    Returns the number of session rows updated.
    """
    await repo.set_setting(UPDATED_AT_KEY, today.isoformat())
    await repo.set_setting(VERSION_KEY, str(SCHEMA_VERSION))
    filled = await repo.backfill_session_cars(cars.all())
    await repo.set_setting(GENERATED_KEY, stamp(cars))
    return filled
