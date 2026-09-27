"""Stint trend (#111): lap time and tyre temperature, lap over lap.

Over a stint the tyres go away and the lap times follow. GT7 broadcasts no
tyre wear, so there are two things to watch it by: how hot the tyres ran on
each lap, and how the lap time drifted. This module puts the two side by
side for a whole session, split into stints, with the drift of each stint as
one figure — "+0.18 s a lap".

A stint ends at a pit stop. The lap a stop happened on belongs to neither
stint: it is slow for a reason that is not the tyres, and the laps after it
are on a different set. GT7 sends no flag for a stop, so a lap is taken for
one by what a stop leaves in a recording (see `is_pit_lap`): fuel that went
up, tyres whose temperature all moved at once, time the car was not being
driven. A partial lap is NOT taken for one. Most of them are a car leaving
the pits, but a lap cut short by a rewind or a missed chicane is partial
too, and calling that a stop would split a stint in the middle of it.

A lap that does not count toward bests — a partial lap, or one ruled out by
hand — is a gap in its stint and not a point on it: its time is not an
attempt at a lap time, and the trend is of attempts.

The drift is a Theil-Sen slope, the median of the slopes between every pair
of laps, and not a least-squares line. One lap spent in traffic or facing
the wrong way moves a least-squares slope by more than a stint of tyre wear
does; it moves a median by nothing, until a third of the laps are like it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Samples = dict[str, list[float]]

WHEELS = ("fl", "fr", "rl", "rr")

# How much longer GT7's lap time may be than the recording's own clock before
# the difference is a stop. The recorder's clock counts the frames the car
# was being driven and stands still while it is not (the lap processor sets
# inactive packets aside), so a lap with a stop in it is shorter on the clock
# than its time by as long as the car stood. Over real laps the two agree to
# a few hundredths, and to 1.8 s at worst on a race's first lap from the
# grid.
PIT_STOP_MIN_S = 5.0
# Fuel going UP by this much, within a lap or between the end of one and the
# start of the next, is a refuel. Fuel otherwise only falls.
REFUEL_MIN = 0.5
# All four tyres changing temperature by this much between one sample and
# the next is a change of tyres: nothing that happens to a tyre on the road
# moves it this fast, let alone four at once. Over 297 real laps the most all
# four moved together in one sample is 6.1 °C, and from the end of one lap to
# the start of the next 1.2 °C. (A single tyre moves faster: 24 °C in a
# sample, locked or against a wall.)
TYRE_CHANGE_MIN_C = 10.0
# The fewest counting laps a drift is taken over. Two laps make a difference,
# not a trend — the rule the consistency figure goes by.
MIN_TREND_LAPS = 3


@dataclass(frozen=True, slots=True)
class LapFigures:
    """What the trend keeps of one lap's samples."""

    # Seconds on the recording's own clock, first sample to last.
    clock_s: float
    # Fuel taken on during the lap: the sum of every rise in the level.
    fuel_added: float
    # Tyre temperature per wheel over the lap, °C. Empty for a recording
    # without the per-wheel columns.
    tyre_avg: dict[str, float]
    tyre_max: dict[str, float]
    # The most all four tyres moved between one sample and the next: the
    # smallest of the four steps, at the sample where that is largest.
    tyre_step: float = 0.0
    # Each wheel's first and last sample, for the step across the line.
    tyre_first: dict[str, float] = field(default_factory=dict)
    tyre_last: dict[str, float] = field(default_factory=dict)


def lap_figures(samples: Samples) -> LapFigures | None:
    """One lap reduced to what the trend needs, or None for a lap without
    samples."""
    t = samples.get("t") or []
    if not t:
        return None
    fuel = samples.get("fuel") or []
    added = sum(max(0.0, b - a) for a, b in zip(fuel, fuel[1:], strict=False))
    tyres = {wheel: samples.get(f"tt_{wheel}") or [] for wheel in WHEELS}
    tyres = {wheel: temps for wheel, temps in tyres.items() if temps}
    step = 0.0
    if len(tyres) == len(WHEELS):
        columns = list(tyres.values())
        step = max(
            (
                min(abs(temps[i + 1] - temps[i]) for temps in columns)
                for i in range(min(len(temps) for temps in columns) - 1)
            ),
            default=0.0,
        )
    return LapFigures(
        clock_s=t[-1] - t[0],
        fuel_added=added,
        tyre_avg={wheel: sum(temps) / len(temps) for wheel, temps in tyres.items()},
        tyre_max={wheel: max(temps) for wheel, temps in tyres.items()},
        tyre_step=step,
        tyre_first={wheel: temps[0] for wheel, temps in tyres.items()},
        tyre_last={wheel: temps[-1] for wheel, temps in tyres.items()},
    )


def _tyres_changed(own: LapFigures, before: LapFigures) -> bool:
    """Whether the tyres the lap began on are not the ones the lap before
    ended on."""
    if len(own.tyre_first) < len(WHEELS) or len(before.tyre_last) < len(WHEELS):
        return False
    return all(
        abs(own.tyre_first[wheel] - before.tyre_last[wheel]) >= TYRE_CHANGE_MIN_C
        for wheel in WHEELS
    )


def is_pit_lap(
    row: dict[str, Any],
    figures: LapFigures | None,
    previous: dict[str, Any] | None = None,
    previous_figures: LapFigures | None = None,
) -> bool:
    """Whether the car was in the pits on this lap.

    Any one of these says so:

      * the lap was ruled out by hand as a pit out-lap;
      * fuel went up, on the lap or since the lap before ended;
      * all four tyres changed temperature at once, on the lap or since
        the lap before ended;
      * GT7's lap time is longer than the recording's clock by a stop's
        worth: the car was not being driven for that long.

    `row` is the lap's summary (repository.lap_summary) and `figures` what
    `lap_figures` made of its samples; `previous` and `previous_figures`
    are the same of the lap driven before it.
    """
    if row.get("exclude_reason") == "pit-out":
        return True
    if figures is not None:
        if figures.fuel_added >= REFUEL_MIN:
            return True
        if figures.tyre_step >= TYRE_CHANGE_MIN_C:
            return True
        if row["time_ms"] / 1000 - figures.clock_s >= PIT_STOP_MIN_S:
            return True
        if previous_figures is not None and _tyres_changed(figures, previous_figures):
            return True
    if previous is not None:
        start, before = row.get("fuel_start"), previous.get("fuel_end")
        if start is not None and before is not None and start - before >= REFUEL_MIN:
            return True
    return False


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def drift(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """(slope, intercept) of the Theil-Sen line through `points`, or None for
    too few of them to have a trend."""
    if len(points) < MIN_TREND_LAPS:
        return None
    slopes = [
        (y2 - y1) / (x2 - x1)
        for i, (x1, y1) in enumerate(points)
        for x2, y2 in points[i + 1 :]
        if x2 != x1
    ]
    if not slopes:
        return None
    slope = _median(slopes)
    return slope, _median([y - slope * x for x, y in points])


def _axle(avg: dict[str, float], left: str, right: str) -> float | None:
    if left not in avg or right not in avg:
        return None
    return (avg[left] + avg[right]) / 2


def _per_lap(points: list[tuple[float, float]], digits: int) -> float | None:
    fitted = drift(points)
    return None if fitted is None else round(fitted[0], digits) + 0.0


def stint_trend(
    rows: list[dict[str, Any]], figures: dict[int, LapFigures]
) -> dict[str, Any]:
    """The session's laps in driving order with their stints.

    `rows` are lap summaries (repository.lap_summary) and `figures` what
    `lap_figures` made of each lap's samples, by lap id.
    """
    ordered = sorted(rows, key=lambda row: (row["number"], row["id"]))
    laps: list[dict[str, Any]] = []
    stint = 1
    opened = False  # whether the current stint has a lap in it yet
    previous: dict[str, Any] | None = None
    for row in ordered:
        own = figures.get(row["id"])
        pit = is_pit_lap(
            row, own, previous, figures.get(previous["id"]) if previous else None
        )
        previous = row
        if pit and opened:
            stint += 1
            opened = False
        counts = bool(row.get("counts_for_best", True)) and row["time_ms"] > 0
        avg = own.tyre_avg if own is not None else {}
        lap: dict[str, Any] = {
            "id": row["id"],
            "number": row["number"],
            "time_ms": row["time_ms"],
            "counts": counts,
            "pit": pit,
            "stint": None if pit else stint,
            "exclude_reason": row.get("exclude_reason") or "",
            "fuel_start": row.get("fuel_start"),
            "tt_front": _rounded(_axle(avg, "fl", "fr")),
            "tt_rear": _rounded(_axle(avg, "rl", "rr")),
            "tt": {
                wheel: {"avg": round(avg[wheel], 1), "max": round(own.tyre_max[wheel], 1)}
                for wheel in WHEELS
                if own is not None and wheel in avg
            },
        }
        if not pit:
            opened = True
        laps.append(lap)

    stints: list[dict[str, Any]] = []
    for n in sorted({lap["stint"] for lap in laps if lap["stint"] is not None}):
        members = [lap for lap in laps if lap["stint"] == n]
        counted = [lap for lap in members if lap["counts"]]
        pace = [(float(lap["number"]), float(lap["time_ms"])) for lap in counted]
        fitted = drift(pace)
        entry: dict[str, Any] = {
            "n": n,
            "first_lap": members[0]["number"],
            "last_lap": members[-1]["number"],
            "laps": len(counted),
            "pace_ms_per_lap": None if fitted is None else round(fitted[0], 1) + 0.0,
            # The fitted line's two ends, for drawing it: [lap, ms].
            "pace_fit": None,
            "tt_front_per_lap": _per_lap(
                [(float(lap["number"]), lap["tt_front"]) for lap in counted
                 if lap["tt_front"] is not None], 2
            ),
            "tt_rear_per_lap": _per_lap(
                [(float(lap["number"]), lap["tt_rear"]) for lap in counted
                 if lap["tt_rear"] is not None], 2
            ),
        }
        if fitted is not None:
            slope, intercept = fitted
            first, last = pace[0][0], pace[-1][0]
            entry["pace_fit"] = [
                [first, round(intercept + slope * first, 1)],
                [last, round(intercept + slope * last, 1)],
            ]
        stints.append(entry)
    return {"laps": laps, "stints": stints}


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 1)
