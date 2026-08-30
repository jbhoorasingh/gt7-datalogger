"""Regenerate backend/app/data/cars.json from the official GT7 car list.

The source is Polyphony's own car list — the hash-stamped JS chunks the page at
gran-turismo.com/gb/gt7/carlist/ renders from, read the same way
`build_track_metadata.py` reads the track list (see app/processing/gt7_assets.py).
It publishes far more than the id and name the pre-#57 CSV carried:
manufacturer, model year, Gr. category, drivetrain, aspiration, displacement,
power, torque, weight and dimensions.

Run it again when a GT7 update adds cars:

    python scripts/build_car_metadata.py [output_path]

The app does this for itself in the background (app/main.py), so this script is
for cutting a release with a current inventory committed to the tree — the
floor every install starts from, before it has any network.

Cars the official list no longer publishes are KEPT from the existing file:
ten ids in the pre-#57 CSV are gone from the site, and someone's recorded
sessions still name them. The merge only ever adds and updates.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.processing import car_source  # noqa: E402
from app.processing.cars import Car, Inventory, dumps, loads  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "app" / "data" / "cars.json"
LEGACY_CSV = Path(__file__).resolve().parents[1] / "data" / "cars.csv"


def read_existing(path: Path) -> Inventory:
    """Whatever inventory is already on disk, so a regeneration never loses a
    car. Falls back to the legacy CSV the first time this runs."""
    if path.exists():
        return loads(path.read_text(encoding="utf-8"))
    if LEGACY_CSV.exists():
        inv = Inventory()
        for row in csv.DictReader(LEGACY_CSV.read_text(encoding="utf-8").splitlines()):
            try:
                car_id = int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
            inv.cars[car_id] = Car(id=car_id, name=row["name"])
        print(f"seeding from the legacy CSV: {len(inv.cars)} names")
        return inv
    return Inventory()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    existing = read_existing(out)
    print(f"fetching {car_source.PAGE}")
    fresh = car_source.fetch_sync()
    merged = car_source.merge(existing, fresh)

    added = sorted(set(fresh.cars) - set(existing.cars))
    kept = sorted(set(existing.cars) - set(fresh.cars))
    changed = sum(
        1 for cid, car in fresh.cars.items() if cid in existing.cars and existing.cars[cid] != car
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(merged) + "\n", encoding="utf-8")

    print(f"official list: {len(fresh.cars)} cars")
    print(f"added: {len(added)}{' ' + str(added[:10]) if added else ''}")
    print(f"updated: {changed}")
    print(f"kept (no longer published): {len(kept)}{' ' + str(kept[:10]) if kept else ''}")
    no_year = sum(1 for c in merged.cars.values() if not c.year)
    print(f"without a model year: {no_year} (race cars and concepts mostly)")
    # Only the freshly fetched cars are checked: the kept ones are name-only
    # rows carried over from the CSV and have no manufacturer by construction.
    no_maker = [c.name for c in fresh.cars.values() if not c.manufacturer]
    if no_maker:
        print(f"WARNING: {len(no_maker)} fetched cars have no manufacturer "
              f"— check the tuners chunk: {no_maker[:5]}")
    print(f"wrote {len(merged.cars)} cars to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
