"""Refresh a running install's car inventory from GT7's own car list.

Usage: python scripts/update_cars.py [output_path]

The app refreshes itself in the background and the admin page has a button for
it (#57); this is the same operation from a shell, for a headless box or a cron
job. All three call app.processing.car_refresh, so they cannot drift apart.

Without an argument it writes where the app looks for a refreshed inventory —
next to the database, NOT into the package. To regenerate the copy committed to
the repository (the one shipped inside the package), use
`scripts/build_car_metadata.py` instead.

Note this only writes the file. A running app picks it up at its next start, or
immediately via Admin -> Update car database.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.processing import car_refresh, car_source  # noqa: E402
from app.processing.cars import CarDatabase  # noqa: E402


async def run(out: Path | None) -> int:
    settings = get_settings()
    destination = out or settings.refreshed_car_inventory()

    # Start from what the app would load, so cars GT7 no longer publishes are
    # carried forward rather than dropped by this run.
    cars = CarDatabase()
    cars.load(settings.car_inventory())
    before = cars.count

    print(f"fetching {car_source.PAGE}")
    try:
        inventory = await car_refresh.fetch_and_store(cars, destination)
    except Exception as exc:
        print(f"update failed: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {len(inventory.cars)} cars to {destination} ({before} before)")
    return 0


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    return asyncio.run(run(out))


if __name__ == "__main__":
    raise SystemExit(main())
