"""Building a car inventory from Polyphony's own car list.

One implementation, three callers: `scripts/build_car_metadata.py` generates
the file committed to the repo, the admin endpoint refreshes it on demand, and
the startup staleness check refreshes it in the background. Before #57 the
first two each had their own copy of a download-and-map against a third-party
mirror; the mapping lives here now, and the mirror is gone.

The source is the same site, and the same hash-stamped JS chunks, that
`scripts/build_track_metadata.py` reads for tracks — see `gt7_assets.py`. Two
chunks are needed: `cars` (one entry per car) and `tuners` (the manufacturer
names the car entries reference by id).
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

import httpx

from app.processing import gt7_assets
from app.processing.cars import SCHEMA_VERSION, Car, Inventory

log = logging.getLogger(__name__)

PAGE = "https://www.gran-turismo.com/gb/gt7/carlist/"
ASSETS = "https://www.gran-turismo.com/common/dist/gt7/carlist/assets/"
LOCALE = "gb"

# Ids are "car102"; the numeric part is the id GT7 broadcasts in telemetry.
CAR_ID = re.compile(r"^car(\d+)$")
# "Skyline GTS-R (R31) '87" -> 87. Anchored at the end: the apostrophes inside
# a name (Mini-Cooper 'S' '65) are not years.
YEAR_SUFFIX = re.compile(r"'(\d{2})\s*$")
# "1932 Ford Roadster", "AFEELA Prototype 2024", "...Vision Gran Turismo, 2017"
# — the handful of cars that spell the year out instead of suffixing it. Only
# at a boundary: opening the name, closing it, or set off by a comma. A
# four-digit number in the MIDDLE of a name is part of the name, not a year —
# "Red Bull X2014 Standard", "HYUNDAI N 2025 Vision Gran Turismo" and "NISSAN
# CONCEPT 2020 Vision Gran Turismo" are all models, and reading those as model
# years would be inventing data. Cars that only date themselves mid-name
# ("Chris Holstrom Concepts 1967 Chevy Nova") keep year 0: unknown is a worse
# answer than the truth and a better one than a confident guess.
YEAR_LONG = re.compile(r"(?:^|,\s*)(19\d{2}|20\d{2})(?![0-9])|(?<![A-Za-z0-9])(19\d{2}|20\d{2})$")
# "PP 440.85"
PP = re.compile(r"([\d.]+)")


def _int(value: Any) -> int:
    """Millimetres and kilogrammes: published as int for most cars and float
    for a few (1,999.5 mm of width), so both have to round-trip."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _displacement(value: Any) -> int:
    """Cubic centimetres, including the rotaries.

    Wankels are published in rotor notation — the 13B is "654x2", the 787B's
    R26B "654x4" — so the product is the swept volume the car is normally
    quoted at (1,308 cc). "-1" is the source's own "not published" (every EV),
    and becomes 0 like any other unknown.
    """
    text = str(value).strip()
    if not text or text == "-1":
        return 0
    if "x" in text:
        head, _, tail = text.partition("x")
        return _int(head) * _int(tail)
    return _int(text)


def _year(short_name: str, long_name: str, pivot: int) -> int:
    """The model year, or 0 for a car whose name carries none.

    Two digits are ambiguous forever: '26 is the AFEELA 1, '29 the Mercedes-Benz
    S Barker Tourer. The pivot is the generation year's own last two digits —
    at or below it the year is this century, above it the last — which is right
    while GT7 keeps adding cars no newer than the day the inventory was built.
    """
    m = YEAR_SUFFIX.search(short_name)
    if m:
        yy = int(m.group(1))
        return 2000 + yy if yy <= pivot else 1900 + yy
    m = YEAR_LONG.search(long_name)
    return int(m.group(1) or m.group(2)) if m else 0


def build_inventory(
    cars_js: str,
    tuners_js: str,
    *,
    generated: str = "",
    pivot: int | None = None,
) -> Inventory:
    """Map the two data chunks onto our schema. Pure: no network, no clock."""
    raw_cars = gt7_assets.parse_js_object(cars_js)
    tuners = gt7_assets.parse_js_object(tuners_js)
    makers = {k: str(v.get("name", "")).strip() for k, v in tuners.items()}
    today = datetime.date.today()
    if pivot is None:
        pivot = today.year % 100
    inv = Inventory(
        generated=generated or today.isoformat(),
        source=PAGE,
        schema_version=SCHEMA_VERSION,
    )
    for key, entry in raw_cars.items():
        m = CAR_ID.match(key)
        if not m:
            log.debug("skipping unrecognised car key %r", key)
            continue
        car_id = int(m.group(1))
        short = str(entry.get("nameShort", "")).strip()
        long_name = str(entry.get("nameLong", "")).strip()
        if not short:
            continue
        pp = PP.search(str(entry.get("performancePoint", "")))
        inv.cars[car_id] = Car(
            id=car_id,
            name=short,
            full_name=long_name,
            manufacturer=makers.get(str(entry.get("manufacturerId", "")), ""),
            year=_year(short, long_name, pivot),
            category=str(entry.get("carClass", "")).strip(),
            # "---" is the source's blank, for the cars with no drivetrain or
            # aspiration to speak of (karts, the X2014s).
            drivetrain=_blank(entry.get("driveTrain")),
            aspiration=_blank(entry.get("aspirationShort")),
            displacement_cc=_displacement(entry.get("displacement_v")),
            power_bhp=_int(entry.get("power_v")),
            torque_kgfm=_float(entry.get("torque_v")),
            weight_kg=_int(entry.get("weight_v")),
            length_mm=_int(entry.get("length_v")),
            width_mm=_int(entry.get("width_v")),
            height_mm=_int(entry.get("height_v")),
            performance_points=_float(pp.group(1)) if pp else 0.0,
        )
    if not inv.cars:
        raise ValueError("car list parsed to nothing; the site layout changed")
    return inv


def _blank(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text == "---" else text


def merge(base: Inventory, fresh: Inventory) -> Inventory:
    """Fresh data wins per car; cars only the old inventory knows are kept.

    GT7's list drops cars (ten ids in the pre-#57 CSV are no longer published),
    and someone's recorded sessions still refer to them. A refresh that took
    those names away would make the app worse at the one thing it already did,
    so the merge only ever adds and updates.
    """
    merged = Inventory(
        cars=dict(base.cars),
        generated=fresh.generated,
        source=fresh.source,
        schema_version=fresh.schema_version,
    )
    merged.cars.update(fresh.cars)
    return merged


def _asset_urls(page_html: str, index_js: str) -> tuple[str, str]:
    cars = gt7_assets.data_chunk_names(index_js, "cars")
    tuners = gt7_assets.data_chunk_names(index_js, "tuners")
    if LOCALE not in cars or LOCALE not in tuners:
        raise ValueError(
            f"car list assets missing the {LOCALE!r} locale "
            f"(cars: {sorted(cars)}, tuners: {sorted(tuners)})"
        )
    return ASSETS + cars[LOCALE], ASSETS + tuners[LOCALE]


async def fetch(client: httpx.AsyncClient) -> Inventory:
    """Walk the site and return a fresh inventory. Raises on any failure."""
    page = (await client.get(PAGE)).raise_for_status().text
    index = gt7_assets.index_chunk_name(page)
    index_js = (await client.get(ASSETS + index)).raise_for_status().text
    cars_url, tuners_url = _asset_urls(page, index_js)
    cars_js = (await client.get(cars_url)).raise_for_status().text
    tuners_js = (await client.get(tuners_url)).raise_for_status().text
    return build_inventory(cars_js, tuners_js)


def fetch_sync() -> Inventory:
    """The same walk over urllib, for the offline build script."""
    page = gt7_assets.http_get(PAGE)
    index_js = gt7_assets.http_get(ASSETS + gt7_assets.index_chunk_name(page))
    cars_url, tuners_url = _asset_urls(page, index_js)
    return build_inventory(gt7_assets.http_get(cars_url), gt7_assets.http_get(tuners_url))
