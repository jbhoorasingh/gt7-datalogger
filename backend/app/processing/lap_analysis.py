"""The lap analysis document: a session as a few hundred labelled numbers (#115).

A lap is stored, exported and synced as its full 60 Hz series — a megabyte or
two of floats. That is the right shape for a chart and the wrong one for
anything that has to REASON about the driving: what a suggestion about lap
time needs is where the driver braked for turn five, how slow the car got,
when the throttle came back and what all of that cost against a better lap.
Those are worked out here, once, into one versioned document per session:

    {"format": "gt7-datalogger-lap-analysis", "version": 1, ...}

Everything in it is measured per corner against a REFERENCE lap — the
session's best counting lap — on that lap's distance axis. Every other lap
is put on the axis by where it was on the road (processing/alignment), and
the corners are the reference's: the circuit's authored corners when it has
them, so that corner 5 is corner 5 in every session, and the ones detected
on the reference lap otherwise. The document says which.

What a lap did at a corner is `corner_metrics`' answer, the same one the
race engineer speaks; time through a corner is `analysis.corner_report`'s,
the same one the report card shows. Nothing is defined twice.

Left out on purpose: the samples, world positions and the resampled series.
All of it can be had from the raw laps, and none of it is what the document
is for.

Conventions, which the document repeats in its own `conventions` block so
that it can be read without this file:

  * distances are metres along the reference lap's driven line;
  * `*_from_apex_m` is metres from the corner's apex, negative before it;
  * `*_vs_ref` is this lap minus the reference: a positive time is time
    lost, a positive speed is speed gained, a positive distance is later on
    the road (braking deeper);
  * an absent key means the lap did not do the thing, or the recording
    cannot say — never zero.

The compiler is fed one lap at a time and keeps only what it derived from
each, so a session of any length costs the memory of two laps: the reference
and the one being read.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from app.models import AidsBits
from app.processing import alignment, analysis
from app.processing.corner_metrics import (
    BRAKE_SEARCH_M,
    THROTTLE_SEARCH_M,
    CornerMeasure,
    LapTrace,
    Window,
    measure,
    windows,
)
from app.processing.track_limits import BorderIndex

Samples = dict[str, list[float]]

FORMAT = "gt7-datalogger-lap-analysis"
VERSION = 1

# Laps a spread is taken over, at least — the Sessions view's figure, for the
# same reason: two laps make a difference, not a spread.
MIN_CONSISTENCY_LAPS = 3
# How far either side of a point the reference's heading is taken over, when
# a lap's line is measured against it.
HEADING_SPAN_M = 5.0
# Slack when judging whether a lap covers a stretch, as corner_report allows.
COVER_TOLERANCE_M = analysis.GRID_TOLERANCE_M
# How far from the line a lap's first or last sample may sit on the
# reference's axis and the lap still have begun, or ended, at the line. The
# first sample after the line is up to a tick past it — 1.4 m at 300 km/h —
# and lining a lap up moves it a few metres more; a lap that began further
# round than this began somewhere else.
LINE_SLACK_M = 30.0

WHEELS = ("fl", "fr", "rl", "rr")

# What the car inventory says about a car, in two kinds. The first are facts
# about the car that no tuning shop changes. The second are the figures of the
# car AS IT LEAVES THE SHOWROOM: GT7 does not broadcast a car's power, weight
# or Performance Points, so for a car that was tuned, ballasted, engine-swapped
# or run under Balance of Performance they are not the session's — and they
# are written under `stock` so that no reader can take them for it.
CAR_FACTS = (
    ("manufacturer", "car_manufacturer"),
    ("year", "car_year"),
    ("drivetrain", "car_drivetrain"),
)
CAR_STOCK = (
    ("aspiration", "car_aspiration"),
    ("displacement_cc", "car_displacement_cc"),
    ("power_bhp", "car_power_bhp"),
    ("torque_kgfm", "car_torque_kgfm"),
    ("weight_kg", "car_weight_kg"),
    ("performance_points", "car_performance_points"),
)

CONVENTIONS: dict[str, str] = {
    "distance": "metres along the reference lap's driven line",
    "from_apex_m": "metres from the corner's apex; negative is before it",
    "vs_ref": (
        "this lap minus the reference lap: a positive time is time lost, a "
        "positive speed is speed gained, a positive distance is later on the road"
    ),
    "speed": "km/h",
    "time": "ms",
    "pedal": "percent of travel",
    "temperature": "degrees Celsius",
    "line": (
        "metres off the reference lap's line at the same place; positive is "
        "tighter (towards the inside of the corner), negative is wider"
    ),
    "edges": "metres from the car's centre to the surveyed road edge",
    "car.stock": (
        "the car as it leaves the showroom, from the published car list; a "
        "tuned or balanced car differs, and its own figures are not recorded"
    ),
    "absent": "a missing key means not done or not known, never zero",
}


def _r(value: float | None, digits: int = 1) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    rounded = round(value, digits)
    return rounded + 0.0  # -0.0 reads as a finding


def _put(target: dict[str, Any], key: str, value: Any) -> None:
    """Set `key` unless there is nothing to say."""
    if value is None:
        return
    if isinstance(value, dict | list) and not value:
        return
    target[key] = value


def _std(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def _through(trace: LapTrace, lo: float, hi: float) -> float | None:
    """Milliseconds the lap took from `lo` to `hi`, or None when it did not
    drive all of it. `lo` past `hi` is a stretch across the start line: the
    tail of the lap plus the head of it, as corner_report times a corner
    there."""
    d = trace.dist
    t = trace.column("t")
    if t is None or len(d) < 2:
        return None
    tol = COVER_TOLERANCE_M

    def clock(dist: float) -> float:
        return analysis._interp(d, t, dist)

    if lo <= hi:
        if lo < d[0] - tol or hi > d[-1] + tol:
            return None
        return (clock(hi) - clock(lo)) * 1000
    if lo > d[-1] + tol or hi > d[-1] + tol or hi < d[0] - tol:
        return None
    return ((t[-1] - clock(lo)) + (clock(hi) - t[0])) * 1000


def _from_the_line(trace: LapTrace, hi: float) -> float | None:
    """Milliseconds from the start of the lap to `hi`. By the lap's own
    clock from its first sample, not from wherever distance 0 falls on its
    axis: the lap began when it began."""
    d = trace.dist
    t = trace.column("t")
    if t is None or len(d) < 2 or d[0] > LINE_SLACK_M or hi > d[-1] + COVER_TOLERANCE_M:
        return None
    return (analysis._interp(d, t, hi) - t[0]) * 1000


def _to_the_line(trace: LapTrace, lo: float, length: float) -> float | None:
    """Milliseconds from `lo` to the end of the lap."""
    d = trace.dist
    t = trace.column("t")
    if t is None or len(d) < 2 or d[-1] < length - LINE_SLACK_M:
        return None
    if lo < d[0] - COVER_TOLERANCE_M or lo > d[-1]:
        return None
    return (t[-1] - analysis._interp(d, t, lo)) * 1000


def _stretch(trace: LapTrace, lo: float, hi: float) -> list[int]:
    """Sample indices inside a stretch, across the line when `lo` > `hi`."""
    if lo <= hi:
        return list(trace.span(lo, hi))
    return list(trace.span(lo, math.inf)) + list(trace.span(-math.inf, hi))


class SessionAnalysis:
    """Compiles one session's document, a lap at a time.

    Built round the reference lap; `add` is then called for every lap of the
    session in any order (the reference included), and `document` returns
    the finished thing. Blocking throughout — corner detection, a walk along
    the reference's path per lap — so callers run each step on a worker
    thread.
    """

    def __init__(
        self,
        *,
        session: dict[str, Any],
        circuit: dict[str, Any],
        reference: dict[str, Any] | None,
        reference_samples: Samples | None,
        authored_corners: list[dict[str, Any]] | None = None,
        authored_sections: list[dict[str, Any]] | None = None,
        borders: BorderIndex | None = None,
        app_version: str = "",
    ) -> None:
        self.session = session
        self.circuit = dict(circuit)
        self.app_version = app_version
        self.borders = borders
        self.reference = reference
        self._laps: list[dict[str, Any]] = []
        # Per lap, what the consistency figures are taken over: corner
        # number -> (brake-on distance, minimum speed, time through).
        self._spread: dict[int, dict[int, tuple[float | None, float | None, float | None]]] = {}
        self._gearing: dict[str, Any] | None = None

        self.ref_samples: Samples | None = None
        self.path: alignment.ReferencePath | None = None
        self.corners: list[dict[str, Any]] = []
        self.windows: list[Window] = []
        self.sections: list[dict[str, Any]] = []
        self.length = 0.0
        self._ref_trace: LapTrace | None = None
        self._ref_measures: dict[int, CornerMeasure] = {}
        self._ref_report: dict[int, analysis.CornerRow] = {}

        if reference is None or not reference_samples or not reference_samples.get("dist"):
            self.reference = None
            return
        self.ref_samples = reference_samples
        self.length = float(reference_samples["dist"][-1])
        if reference_samples.get("pos_x") and reference_samples.get("pos_z"):
            path = alignment.ReferencePath(reference_samples)
            self.path = path if path.segments else None
        self.corners = [
            dict(c) for c in analysis.corners_for_lap(reference_samples, authored_corners or [])
        ]
        self.windows = windows(self.corners)
        self.sections = [
            dict(sec)
            for sec in analysis.project_sections(reference_samples, authored_sections or [])
        ]
        self._ref_trace = LapTrace(reference_samples)
        self._ref_measures = measure(self._ref_trace, self.windows)
        self._ref_report = {
            row["n"]: row
            for row in analysis.corner_report(
                self.corners, reference_samples, measures=self._ref_measures
            )
        }

    # --- one lap --------------------------------------------------------------

    def add(
        self,
        row: dict[str, Any],
        samples: Samples,
        events: list[dict[str, Any]] | None = None,
        gearing: dict[str, Any] | None = None,
    ) -> None:
        """Take one lap in. `row` is the lap's summary (repository.lap_summary),
        `samples` its stored series."""
        events = events or []
        if gearing and self._gearing is None:
            self._gearing = gearing
        is_reference = self.reference is not None and row["id"] == self.reference["id"]
        lap: dict[str, Any] = {
            "id": row["id"],
            "number": row["number"],
            "time_ms": row["time_ms"],
        }
        if self.reference is not None:
            lap["time_vs_ref"] = row["time_ms"] - self.reference["time_ms"]
        if is_reference:
            lap["reference"] = True
        lap["counts_for_best"] = bool(row.get("counts_for_best", True))
        clean = row.get("clean_lap")
        lap["clean"] = None if clean is None else bool(clean)
        _put(lap, "exclude_reason", row.get("exclude_reason") or None)
        if row.get("salvaged"):
            lap["salvaged"] = True
        if (row.get("race_position") or -1) > 0:
            lap["race_position"] = row["race_position"]
        lap.update(self._context(row, samples))

        on_axis, aligned = samples, is_reference
        if self.ref_samples is not None and not is_reference and self.path is not None:
            moved = alignment.align_to_reference(samples, self.path)
            if moved is not None:
                events = alignment.remap_events(events, samples.get("dist") or [], moved["dist"])
                on_axis, aligned = moved, True
        if self.ref_samples is not None:
            lap["aligned"] = aligned
            trace = LapTrace(on_axis)
            corners = self._corners(trace, on_axis, events, is_reference, aligned, row["id"])
            _put(lap, "corners", corners)
            _put(lap, "sections", self._sections(trace, is_reference))
            _put(lap, "to_line", self._to_line(trace, is_reference))
        _put(lap, "shifts", _shifts(samples))
        self._laps.append(lap)

    def _context(self, row: dict[str, Any], samples: Samples) -> dict[str, Any]:
        out: dict[str, Any] = {
            "fuel": {
                "start": _r(row.get("fuel_start"), 2),
                "end": _r(row.get("fuel_end"), 2),
                "used": _r(row.get("fuel_consumed"), 3),
            },
            "pedals": {
                "full_throttle_pct": _r(row.get("full_throttle_pct")),
                "full_brake_pct": _r(row.get("full_brake_pct")),
                "coasting_pct": _r(row.get("coasting_pct")),
            },
            "aids": {
                "tcs_pct": _r(row.get("tcs_active_pct")),
                "asm_pct": _r(row.get("asm_active_pct")),
            },
            "tire_spin_pct": _r(row.get("tire_spin_pct")),
            "max_speed": _r(row.get("max_speed")),
        }
        tyres: dict[str, Any] = {}
        for wheel in WHEELS:
            temps = samples.get(f"tt_{wheel}") or []
            if temps:
                tyres[wheel] = {
                    "avg": _r(sum(temps) / len(temps)),
                    "max": _r(max(temps)),
                    "end": _r(temps[-1]),
                }
        _put(out, "tyre_temp", tyres)
        counts = dict(row.get("event_counts") or {})
        _put(out, "events", counts)
        # -1 is "could not be judged", which is not a count of nought.
        for key in ("off_track_count", "off_survey_count"):
            value = row.get(key)
            if isinstance(value, int) and value >= 0:
                out[key.removesuffix("_count")] = value
        return out

    def _corners(
        self,
        trace: LapTrace,
        samples: Samples,
        events: list[dict[str, Any]],
        is_reference: bool,
        aligned: bool,
        lap_id: int,
    ) -> list[dict[str, Any]]:
        measures = measure(trace, self.windows)
        report = {
            row["n"]: row
            for row in analysis.corner_report(self.corners, samples, measures=measures)
        }
        by_n = {int(c["n"]): c for c in self.corners}
        aids = trace.column("aids")
        spread: dict[int, tuple[float | None, float | None, float | None]] = {}
        out: list[dict[str, Any]] = []
        previous: Window | None = None
        for window in self.windows:
            timed = report.get(window.n)
            before, previous = previous, window
            if timed is None:
                continue  # the lap never drove the whole of this corner
            m = measures[window.n]
            ref_m = self._ref_measures.get(window.n)
            ref_timed = self._ref_report.get(window.n)
            compare = not is_reference
            corner: dict[str, Any] = {"n": window.n, "time_ms": _r(float(timed["time_ms"]))}
            if compare and ref_timed is not None:
                corner["time_vs_ref"] = _r(float(timed["time_ms"]) - float(ref_timed["time_ms"]))

            speed: dict[str, Any] = {
                "entry": timed["entry_speed"],
                "min": timed["min_speed"],
                "exit": timed["exit_speed"],
            }
            if compare and ref_timed is not None:
                speed["min_vs_ref"] = _r(
                    float(timed["min_speed"]) - float(ref_timed["min_speed"])
                )
                speed["exit_vs_ref"] = _r(
                    float(timed["exit_speed"]) - float(ref_timed["exit_speed"])
                )
            if m.min_speed_dist is not None:
                speed["min_from_apex_m"] = _r(m.min_speed_dist - window.apex)
            _put(speed, "min_gear", m.min_speed_gear)
            corner["speed"] = speed

            _put(corner, "braking", self._braking(m, ref_m, window, events, compare))
            _put(corner, "turn_in", _turn_in(m, window))
            _put(corner, "throttle", _throttle(m, window, events, trace, aids))
            if not window.wraps:
                direction = str(by_n[window.n]["direction"])
                if compare and aligned:
                    _put(corner, "line", self._line(trace, window, direction))
                _put(corner, "edges", self._edges(trace, window, direction))
                _put(corner, "approach", self._approach(trace, before, window, compare))
            out.append(corner)
            spread[window.n] = (m.brake_on, m.min_speed, float(timed["time_ms"]))
        self._spread[lap_id] = spread
        return out

    def _braking(
        self,
        m: CornerMeasure,
        ref_m: CornerMeasure | None,
        window: Window,
        events: list[dict[str, Any]],
        compare: bool,
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if m.brake_off is not None:
            if m.brake_on is not None:
                out["on_from_apex_m"] = _r(m.brake_on - window.apex)
                if compare and ref_m is not None and ref_m.brake_on is not None:
                    out["on_vs_ref"] = _r(m.brake_on - ref_m.brake_on)
                out["length_m"] = _r(m.brake_off - m.brake_on)
            out["off_from_apex_m"] = _r(m.brake_off - window.apex)
            out["peak_pct"] = _r(m.brake_peak)
            _put(out, "speed_on", _r(m.brake_on_speed))
            _put(out, "speed_off", _r(m.brake_off_speed))
        elif compare and ref_m is not None and ref_m.brake_on is not None:
            # The reference braked here and this lap did not: that is a
            # finding, and an absent block would hide it.
            out["none"] = True
        lo = window.entry - BRAKE_SEARCH_M
        if window.prev_apex is not None:
            lo = max(lo, window.prev_apex)
        lockups = _count(events, "lockup", lo, window.apex)
        if lockups:
            out["lockups"] = lockups
        return out

    def _line(self, trace: LapTrace, window: Window, direction: str) -> dict[str, Any]:
        """Where the lap ran against the reference's line, at the corner's
        three marks. Only for a lap that was lined up with the reference:
        "the same place" is what the alignment is."""
        ref = self._ref_trace
        if ref is None:
            return {}
        out: dict[str, Any] = {}
        for name, dist in (("entry_m", window.entry), ("apex_m", window.apex),
                           ("exit_m", window.exit)):
            here = _position(trace, dist)
            there = _position(ref, dist)
            a = _position(ref, max(ref.dist[0], dist - HEADING_SPAN_M))
            b = _position(ref, min(ref.dist[-1], dist + HEADING_SPAN_M))
            if here is None or there is None or a is None or b is None:
                continue
            hx, hz = b[0] - a[0], b[1] - a[1]
            norm = math.hypot(hx, hz)
            if norm < 1e-6:
                continue
            # The cross product's sign in raw x/z: positive is the side a
            # right-hander turns towards (GT7's z runs the other way up from
            # the map's, see analysis._turn_direction).
            side = (hx * (here[1] - there[1]) - hz * (here[0] - there[0])) / norm
            out[name] = _r(side if direction == "R" else -side, 2)
        return out

    def _edges(self, trace: LapTrace, window: Window, direction: str) -> dict[str, Any]:
        borders = self.borders
        if borders is None:
            return {}
        inside, outside = ("R", "L") if direction == "R" else ("L", "R")
        ys = trace.column("pos_y")
        out: dict[str, Any] = {}
        for name, dist in (("entry", window.entry), ("apex", window.apex),
                           ("exit", window.exit)):
            here = _position(trace, dist)
            if here is None:
                continue
            y = trace.at("pos_y", dist) if ys is not None else None
            mark: dict[str, Any] = {}
            _put(mark, "inside_m", _r(borders.distance(inside, here[0], here[1], y), 2))
            _put(mark, "outside_m", _r(borders.distance(outside, here[0], here[1], y), 2))
            _put(out, name, mark)
        return out

    def _approach(
        self, trace: LapTrace, before: Window | None, window: Window, compare: bool
    ) -> dict[str, Any]:
        """The road between the corner before and this one: what a poor exit
        costs is lost here, not in either corner's own window."""
        if before is not None and before.wraps:
            return {}
        lo = before.exit if before is not None else 0.0
        hi = window.entry
        if hi - lo < 1.0:
            return {}

        def took_by(lap: LapTrace) -> float | None:
            return _from_the_line(lap, hi) if before is None else _through(lap, lo, hi)

        took = took_by(trace)
        if took is None:
            return {}
        out: dict[str, Any] = {"length_m": _r(hi - lo), "time_ms": _r(took)}
        if compare and self._ref_trace is not None:
            theirs = took_by(self._ref_trace)
            if theirs is not None:
                out["time_vs_ref"] = _r(took - theirs)
        speed = trace.column("speed")
        inside = trace.span(lo, hi)
        if speed is not None and len(inside):
            out["max_speed"] = _r(max(speed[i] for i in inside))
        return out

    def _to_line(self, trace: LapTrace, is_reference: bool) -> dict[str, Any]:
        """From the last corner's exit to the finish."""
        if not self.windows or self.windows[-1].wraps:
            return {}
        lo = self.windows[-1].exit
        took = _to_the_line(trace, lo, self.length)
        if took is None or self.length - lo < 1.0:
            return {}
        out: dict[str, Any] = {"length_m": _r(self.length - lo), "time_ms": _r(took)}
        if not is_reference and self._ref_trace is not None:
            theirs = _to_the_line(self._ref_trace, lo, self.length)
            if theirs is not None:
                out["time_vs_ref"] = _r(took - theirs)
        speed = trace.column("speed")
        inside = trace.span(lo, self.length)
        if speed is not None and len(inside):
            out["max_speed"] = _r(max(speed[i] for i in inside))
        return out

    def _sections(self, trace: LapTrace, is_reference: bool) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        speed = trace.column("speed")
        gear = trace.column("gear")
        rpm = trace.column("rpm")
        for section in self.sections:
            lo, hi = float(section["start_dist"]), float(section["end_dist"])
            took = _through(trace, lo, hi)
            if took is None:
                continue
            entry: dict[str, Any] = {"n": section["n"], "time_ms": _r(took)}
            if not is_reference and self._ref_trace is not None:
                theirs = _through(self._ref_trace, lo, hi)
                if theirs is not None:
                    entry["time_vs_ref"] = _r(took - theirs)
            inside = _stretch(trace, lo, hi)
            if speed is not None and inside:
                _put(entry, "entry_speed", _r(trace.at("speed", lo)))
                # The speed trap: the fastest the car went in the section,
                # and what the engine was doing when it did.
                top = max(inside, key=lambda i: speed[i])
                trap: dict[str, Any] = {"speed": _r(speed[top])}
                if lo <= hi:
                    trap["from_end_m"] = _r(trace.dist[top] - hi)
                if gear is not None:
                    trap["gear"] = int(gear[top])
                if rpm is not None:
                    trap["rpm"] = round(rpm[top])
                entry["top"] = trap
                end: dict[str, Any] = {}
                _put(end, "speed", _r(trace.at("speed", hi)))
                at_end = trace.index(hi)
                if gear is not None:
                    end["gear"] = int(gear[at_end])
                if rpm is not None:
                    end["rpm"] = round(rpm[at_end])
                _put(entry, "end", end)
            out.append(entry)
        return out

    # --- the whole session ----------------------------------------------------

    def _consistency(self) -> dict[str, Any] | None:
        counting = [lap for lap in self._laps if lap["counts_for_best"] and lap["time_ms"] > 0]
        out: dict[str, Any] = {}
        if len(counting) >= MIN_CONSISTENCY_LAPS:
            times = [float(lap["time_ms"]) for lap in counting]
            std, median = _std(times), _median(times)
            # The Sessions view's figure (frontend/src/lib/consistency.ts),
            # over the same laps: every lap that counts towards the bests.
            out["lap_time"] = {
                "laps": len(times),
                "best_ms": round(min(times)),
                "median_ms": round(median),
                "std_ms": _r(std),
                "pct": _r(std / median * 100, 3) if median else None,
            }

        # Per corner, over laps that were lined up with the reference and
        # driven cleanly. A lap GT7 and the survey both passed is clean; one
        # neither could judge is not known to be dirty, and a session
        # recorded without surface data would otherwise have no figures at
        # all — so the unjudged laps stand in when too few were verified.
        usable = [lap for lap in counting if lap.get("aligned") and lap["id"] in self._spread]
        clean = [lap for lap in usable if lap["clean"] is True]
        if len(clean) >= MIN_CONSISTENCY_LAPS:
            basis, chosen = "clean", clean
        else:
            basis, chosen = "counting", [lap for lap in usable if lap["clean"] is not False]
        corners: list[dict[str, Any]] = []
        if len(chosen) >= MIN_CONSISTENCY_LAPS:
            for window in self.windows:
                rows = [self._spread[lap["id"]].get(window.n) for lap in chosen]
                entry: dict[str, Any] = {"n": window.n}
                for index, key in enumerate(("brake_on", "min_speed", "time")):
                    values: list[float] = []
                    for measured in rows:
                        value = measured[index] if measured is not None else None
                        if value is not None:
                            values.append(value)
                    if len(values) < MIN_CONSISTENCY_LAPS:
                        continue
                    entry[key] = {
                        "laps": len(values),
                        "std": _r(_std(values)),
                        "spread": _r(max(values) - min(values)),
                    }
                if len(entry) > 1:
                    corners.append(entry)
        if corners:
            out["corners"] = {
                "basis": basis,
                "laps": sorted(lap["number"] for lap in chosen),
                "units": {"brake_on": "m", "min_speed": "km/h", "time": "ms"},
                "by_corner": corners,
            }
        return out or None

    def document(self) -> dict[str, Any]:
        session = self.session
        laps = sorted(self._laps, key=lambda lap: (lap["number"], lap["id"]))
        car: dict[str, Any] = {
            "id": session.get("car_id"),
            "name": session.get("car_name") or "",
            "category": session.get("car_category") or "",
        }
        for key, source in CAR_FACTS:
            _put(car, key, session.get(source) or None)
        stock: dict[str, Any] = {}
        for key, source in CAR_STOCK:
            _put(stock, key, session.get(source) or None)
        _put(car, "stock", stock)
        if self._gearing:
            gearing: dict[str, Any] = {}
            _put(gearing, "ratios", self._gearing.get("ratios"))
            _put(gearing, "rpm_alert", self._gearing.get("rpm_alert") or None)
            _put(car, "gearing", gearing)

        circuit = dict(self.circuit)
        if self.length:
            circuit["lap_length_m"] = _r(self.length)
        circuit["corners"] = (
            "none" if not self.corners
            else "authored" if self.corners[0].get("authored") else "detected"
        )
        circuit["sections"] = "authored" if self.sections else "none"
        circuit["surveyed"] = self.borders is not None

        summary: dict[str, Any] = {
            "id": session.get("id"),
            "started_at": session.get("started_at"),
            "laps": len(laps),
            "counting_laps": sum(1 for lap in laps if lap["counts_for_best"]),
        }
        _put(summary, "note", session.get("note") or None)
        _put(summary, "tags", session.get("tags"))
        if session.get("bests_excluded"):
            summary["bests_excluded"] = True
        if (session.get("final_position") or 0) > 0:
            race: dict[str, Any] = {"position": session["final_position"]}
            for key, source in (
                ("of", "final_total_positions"), ("laps", "race_laps"),
                ("time_ms", "race_time_ms"),
            ):
                if (session.get(source) or 0) > 0:
                    race[key] = session[source]
            summary["race"] = race

        reference: dict[str, Any] | None = None
        if self.reference is not None:
            reference = {
                "lap_id": self.reference["id"],
                "number": self.reference["number"],
                "time_ms": self.reference["time_ms"],
                "scope": "session",
                "why": "the fastest lap of this session that counts towards the bests",
            }

        return {
            "format": FORMAT,
            "version": VERSION,
            "compiled_at": datetime.now(UTC).isoformat(),
            "app_version": self.app_version,
            "conventions": CONVENTIONS,
            "session": summary,
            "car": car,
            "circuit": circuit,
            "reference": reference,
            "corners": [_corner_entry(c, w) for c, w in self._corner_pairs()],
            "sections": [
                {
                    "n": sec["n"],
                    "name": sec["name"],
                    "start_m": sec["start_dist"],
                    "end_m": sec["end_dist"],
                    "length_m": _r(
                        float(sec["end_dist"]) - float(sec["start_dist"])
                        if float(sec["end_dist"]) >= float(sec["start_dist"])
                        else self.length - float(sec["start_dist"]) + float(sec["end_dist"])
                    ),
                }
                for sec in self.sections
            ],
            "laps": laps,
            "consistency": self._consistency(),
        }

    def _corner_pairs(self) -> list[tuple[dict[str, Any], Window]]:
        by_n = {int(c["n"]): c for c in self.corners}
        return [(by_n[w.n], w) for w in self.windows]


# --- pieces -------------------------------------------------------------------


def _corner_entry(corner: dict[str, Any], window: Window) -> dict[str, Any]:
    out: dict[str, Any] = {"n": window.n}
    _put(out, "name", corner.get("name") or None)
    out["direction"] = corner.get("direction")
    out["entry_m"] = _r(window.entry)
    out["apex_m"] = _r(window.apex)
    out["exit_m"] = _r(window.exit)
    _put(out, "angle_deg", corner.get("angle_deg"))
    if window.wraps:
        # Stitched across the start line: timed, but nothing that needs the
        # approach to it, which is on the lap before.
        out["crosses_start"] = True
    return out


def _position(trace: LapTrace, dist: float) -> tuple[float, float] | None:
    x, z = trace.at("pos_x", dist), trace.at("pos_z", dist)
    if x is None or z is None:
        return None
    return x, z


def _count(events: list[dict[str, Any]], kind: str, lo: float, hi: float) -> int:
    return sum(
        1
        for event in events
        if event.get("type") == kind and lo <= float(event.get("start_dist", -1.0)) <= hi
    )


def _turn_in(m: CornerMeasure, window: Window) -> dict[str, Any]:
    if m.turn_in is None:
        return {}
    out: dict[str, Any] = {"from_apex_m": _r(m.turn_in - window.apex)}
    _put(out, "brake_pct", _r(m.brake_at_turn_in))
    # How far the brake was carried into the corner. Nought is a finding
    # here — the brake was off before the car turned — so it is written.
    _put(out, "trail_m", _r(m.trail_m))
    return out


def _throttle(
    m: CornerMeasure,
    window: Window,
    events: list[dict[str, Any]],
    trace: LapTrace,
    aids: list[float] | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if m.flat is not None:
        out["flat"] = m.flat
    if m.throttle_on is not None:
        out["on_from_apex_m"] = _r(m.throttle_on - window.apex)
    if m.throttle_full is not None:
        out["full_from_apex_m"] = _r(m.throttle_full - window.apex)
    hi = window.exit + THROTTLE_SEARCH_M
    if window.next_apex is not None:
        hi = min(hi, window.next_apex)
    spins = _count(events, "wheelspin", window.apex, hi)
    if spins:
        out["wheelspin"] = spins
    if aids is not None:
        exit_run = trace.span(window.apex, window.exit)
        if len(exit_run):
            on = sum(1 for i in exit_run if int(aids[i]) & AidsBits.TCS)
            if on:
                out["tcs_pct"] = _r(100.0 * on / len(exit_run))
    return out


def _shifts(samples: Samples) -> list[dict[str, Any]]:
    """Each gear's upshifts over the lap: how many, and the engine speed they
    were made at — to be read against the car's `rpm_alert`."""
    gear = samples.get("gear") or []
    rpm = samples.get("rpm") or []
    n = min(len(gear), len(rpm))
    by_gear: dict[int, list[float]] = {}
    for i in range(1, n):
        was, now = int(gear[i - 1]), int(gear[i])
        if was >= 1 and now == was + 1:
            by_gear.setdefault(was, []).append(rpm[i - 1])
    return [
        {
            "from_gear": g,
            "count": len(values),
            "rpm_avg": round(sum(values) / len(values)),
            "rpm_min": round(min(values)),
            "rpm_max": round(max(values)),
        }
        for g, values in sorted(by_gear.items())
    ]
