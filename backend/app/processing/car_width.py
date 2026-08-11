"""Measured axle track width per car, remembered across runs.

Width is a property of the car, not the circuit, so it has no business in a
track bundle — but it is just as wasteful to re-derive it every session. GT7
broadcasts the car id (`carCode` in packet A), so a width measured once is a
width known from the first tick of every later run in that car.

That matters because the width is applied to EVERY derived contact point: a
run that has not measured yet lays its early points at the 1.6 m assumption
and can never retroactively move them. Remembering the car closes that
window from "however long until the first corner" to nothing.

Kept in its own small JSON file rather than the database: it is working data
like the bundles, it has to survive independently of any schema work (#14),
and one file per concern keeps both trivially inspectable.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

FORMAT = "gt7-datalogger-car-widths"
VERSION = 1
FILENAME = "car-widths.json"


def path_for(data_dir: Path) -> Path:
    return data_dir / FILENAME


def load(data_dir: Path) -> dict[int, dict[str, Any]]:
    """car id -> {width_m, samples, car, updated_at}; empty when unreadable."""
    path = path_for(data_dir)
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("format") != FORMAT or doc.get("version", 1) > VERSION:
            return {}
        cars: dict[str, Any] = doc.get("cars", {})
        return {int(k): v for k, v in cars.items()}
    except (ValueError, TypeError, OSError) as exc:
        log.warning("unreadable car width file %s: %s", path, exc)
        return {}


def remember(
    data_dir: Path, car_id: int, width_m: float, samples: int, car_name: str = ""
) -> dict[int, dict[str, Any]]:
    """Record a measurement, keeping whichever has more samples behind it.

    A three-corner run must not overwrite a value settled over a full
    session; confidence here is just how much cornering went into it.
    """
    cars = load(data_dir)
    prior = cars.get(car_id)
    if prior is not None and prior.get("samples", 0) >= samples:
        return cars
    cars[car_id] = {
        "width_m": round(width_m, 3),
        "samples": samples,
        "car": car_name,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "cars": {str(k): v for k, v in sorted(cars.items())},
    }
    path = path_for(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)  # atomic: a crash never leaves a half-written file
    log.info("axle track remembered for car %s%s: %.3f m (%d samples)",
             car_id, f" ({car_name})" if car_name else "", width_m, samples)
    return cars
