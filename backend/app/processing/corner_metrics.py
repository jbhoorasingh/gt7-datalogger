"""What a lap did at each corner: braking, turn-in, minimum speed, throttle,
and how far the car was rotated against its own direction of travel.

One definition of each, shared by everything that talks about a corner — the
race engineer's spoken coaching (#110), the lap analysis document (#115) and
the corner report card.
Before this the braking point lived in the coaching detector alone, and was
"the first brake application in the 250 m before the corner's entry". On a
real circuit that is often another corner's: through a sequence the window
before turn 13 holds turn 11's braking zone, and a brake application that
began after the entry marker (an authored corner's window is the apex
± 75 m, not where the driver turned in) was not found at all. On the best
laps of nine stored sessions it disagreed with where the corner's braking
actually began at 41 of 105 corners — 13 of Mount Panorama's 23.

So braking is attributed the other way round. The lap's brake applications
are found first, each is given to ONE corner — the first apex its midpoint
has not yet reached — and a corner's braking zone is the application, of
those it was given, that took the most speed off. The midpoint rather than
the onset, so that trail braking carried a few metres past an apex still
belongs to that apex and a stab begun a few metres before a kink belongs to
the corner it was really for.

Everything here is measured on whatever distance axis the samples carry. To
compare two laps the caller puts both on one axis first
(`alignment.align_to_reference`); the corner windows are the reference
lap's, so every lap is measured through the same stretch of road.

Distances are metres along that axis, speeds km/h, pedals percent, angles
degrees.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from typing import Any

Samples = dict[str, list[float]]

# The pedal position that counts as braking — the gate processing/events.py
# uses for a lockup, so "braking" means one thing everywhere.
BRAKE_ON_PCT = 20.0
# How far before a corner's entry its braking zone may begin. A brake
# application further back than this is about something else (traffic, a
# mistake on the straight) and belongs to no corner.
BRAKE_SEARCH_M = 250.0
# A brake application shorter than this is a brush of the pedal, not a zone.
BRAKE_MIN_S = 0.1
# Two applications this close together are one: the pedal dipping under the
# gate for a moment while the driver modulates it.
BRAKE_MERGE_GAP_M = 10.0

# The throttle position that counts as "back on the power", and as flat.
THROTTLE_ON_PCT = 20.0
THROTTLE_FULL_PCT = 98.0
# How far past a corner's exit the throttle points are still looked for.
# Never past the next corner's apex, whatever this says.
THROTTLE_SEARCH_M = 100.0

# Turn-in is where the yaw rate climbs through this share of the corner's
# peak on the way to it. A share rather than a figure because a hairpin and
# a fast sweeper peak an order of magnitude apart.
TURN_IN_YAW_SHARE = 0.25
# Below this the car is not turning, whatever share of the peak it is
# (rad/s; a corner whose PEAK is under it has no turn-in to find).
TURN_IN_YAW_FLOOR = 0.03
# How far before the apex turn-in is looked for.
TURN_IN_SEARCH_M = 200.0

# The fewest samples a corner's body slip is summed up from. A corner the lap
# crossed in a handful of ticks has a reading, not a balance.
SLIP_MIN_SAMPLES = 5


@dataclass(frozen=True, slots=True)
class Window:
    """One corner's stretch of the axis, and what bounds the search round it."""

    n: int
    entry: float
    apex: float
    exit: float
    # The apex before this one and the one after, which is where this
    # corner's braking and throttle stop being its own. None at the ends of
    # the lap.
    prev_apex: float | None
    next_apex: float | None
    # "L" or "R", or empty where the corner does not say. Only the body slip
    # needs it: which way round the corner goes is which way "into it" is.
    direction: str = ""

    @property
    def wraps(self) -> bool:
        """A corner stitched across the start line: its entry is at the end
        of the lap and its exit at the start of the next."""
        return self.entry > self.exit


@dataclass(frozen=True, slots=True)
class BrakeRun:
    """One brake application, as indices into the trace."""

    on: int
    off: int
    # False when the pedal was already down at the lap's first sample: the
    # application began on the lap before and its onset is not in this one.
    onset_known: bool = True


@dataclass(slots=True)
class CornerMeasure:
    """What one lap did at one corner. None wherever the lap did not do the
    thing (no braking into a flat kink) or the recording cannot say."""

    n: int
    # --- braking
    brake_on: float | None = None
    brake_off: float | None = None
    brake_peak: float | None = None
    brake_on_speed: float | None = None
    brake_off_speed: float | None = None
    # --- turn-in
    turn_in: float | None = None
    brake_at_turn_in: float | None = None
    # Metres the brake stayed on past turn-in; 0 when it was released first.
    trail_m: float | None = None
    # --- the slowest point
    min_speed: float | None = None
    min_speed_dist: float | None = None
    min_speed_gear: int | None = None
    # --- throttle
    # True when the throttle never came under THROTTLE_ON_PCT: the corner
    # was taken flat and there is no pick-up point to report.
    flat: bool | None = None
    throttle_on: float | None = None
    throttle_full: float | None = None
    # --- balance (#109)
    # Body slip between entry and exit, turned so that positive is the nose
    # pointing INTO the corner, further round than the car is travelling:
    # rotation. `slip_peak` is the most of it the corner saw, `slip_mean`
    # the corner's average. Negative is the nose pointing out of the corner.
    slip_peak: float | None = None
    slip_mean: float | None = None


def windows(corners: list[dict[str, Any]]) -> list[Window]:
    """The corners of a lap (`corners_for_lap`'s, or any list with the same
    keys) as search windows, in the order they are driven.

    A corner without an `apex_dist` — the hand-built ones in tests, and
    nothing `corners_for_lap` returns — takes the middle of its window.
    """
    placed: list[tuple[float, int, float, float, str]] = []
    for corner in corners:
        entry, exit_ = float(corner["entry_dist"]), float(corner["exit_dist"])
        apex = corner.get("apex_dist")
        if apex is None:
            apex = (entry + exit_) / 2 if entry <= exit_ else exit_
        direction = str(corner.get("direction") or "")
        placed.append((float(apex), int(corner["n"]), entry, exit_, direction))
    placed.sort()
    out: list[Window] = []
    for i, (apex, n, entry, exit_, direction) in enumerate(placed):
        out.append(
            Window(
                n=n,
                entry=entry,
                apex=apex,
                exit=exit_,
                prev_apex=placed[i - 1][0] if i > 0 else None,
                next_apex=placed[i + 1][0] if i + 1 < len(placed) else None,
                direction=direction,
            )
        )
    return out


class LapTrace:
    """A lap's samples on a strictly increasing distance axis, with its brake
    applications found once.

    Strictly increasing because distance is the axis everything is looked up
    on: a pause, an import or an aligned lap holding its place through a
    spin repeats a distance, and a repeated distance has no single answer to
    "what was the speed there". The first sample at each distance is kept,
    as it is everywhere else distance is an axis.
    """

    def __init__(self, samples: Samples) -> None:
        dist = samples.get("dist") or []
        keep: list[int] = []
        last = float("-inf")
        for i, d in enumerate(dist):
            if d > last:
                keep.append(i)
                last = d
        self._keep = keep
        self._samples = samples
        self._columns: dict[str, list[float] | None] = {}
        self.dist: list[float] = [dist[i] for i in keep]
        self.runs: list[BrakeRun] = self._brake_runs()

    def __len__(self) -> int:
        return len(self.dist)

    def column(self, name: str) -> list[float] | None:
        """A column on the trace's axis, or None when the lap has no such
        channel (or not all of it)."""
        if name not in self._columns:
            raw = self._samples.get(name)
            usable = raw is not None and bool(self._keep) and len(raw) > self._keep[-1]
            self._columns[name] = [raw[i] for i in self._keep] if usable and raw else None
        return self._columns[name]

    def index(self, dist: float) -> int:
        """The first sample at or past `dist`, clamped to the lap."""
        return min(max(bisect_left(self.dist, dist), 0), max(len(self.dist) - 1, 0))

    def span(self, lo: float, hi: float) -> range:
        """Indices of the samples with lo <= dist <= hi."""
        return range(bisect_left(self.dist, lo), bisect_right(self.dist, hi))

    def at(self, name: str, dist: float) -> float | None:
        """A column's value at a distance, interpolated. None off the ends of
        the lap: the value a little past the last sample is not the last
        sample's."""
        col = self.column(name)
        d = self.dist
        if col is None or not d or dist < d[0] or dist > d[-1]:
            return None
        i = bisect_left(d, dist)
        if d[i] == dist or i == 0:
            return col[i]
        x0, x1 = d[i - 1], d[i]
        return col[i - 1] + (col[i] - col[i - 1]) * (dist - x0) / (x1 - x0)

    def _brake_runs(self) -> list[BrakeRun]:
        brake = self.column("brake")
        d = self.dist
        if brake is None or len(d) < 2:
            return []
        t = self.column("t")
        raw: list[tuple[int, int]] = []
        start: int | None = None
        for i, value in enumerate(brake):
            if value >= BRAKE_ON_PCT:
                if start is None:
                    start = i
            elif start is not None:
                raw.append((start, i - 1))
                start = None
        if start is not None:
            raw.append((start, len(brake) - 1))

        merged: list[tuple[int, int]] = []
        for on, off in raw:
            if merged and d[on] - d[merged[-1][1]] < BRAKE_MERGE_GAP_M:
                merged[-1] = (merged[-1][0], off)
            else:
                merged.append((on, off))

        runs: list[BrakeRun] = []
        for on, off in merged:
            # By the clock where there is one; a lap without it (nothing the
            # logger records) is held to two samples instead.
            long_enough = (t[off] - t[on] >= BRAKE_MIN_S) if t is not None else off > on
            if long_enough:
                runs.append(BrakeRun(on, off, onset_known=on > 0))
        return runs


def _zone(trace: LapTrace, window: Window) -> BrakeRun | None:
    """The brake application that slowed the car for this corner."""
    if window.wraps:
        return None
    d = trace.dist
    speed = trace.column("speed")
    best: BrakeRun | None = None
    best_drop = float("-inf")
    for run in trace.runs:
        mid = (d[run.on] + d[run.off]) / 2
        if mid > window.apex:
            break  # runs are in driving order: the rest are further on still
        if window.prev_apex is not None and mid <= window.prev_apex:
            continue
        if d[run.on] < window.entry - BRAKE_SEARCH_M:
            continue
        drop = (speed[run.on] - speed[run.off]) if speed is not None else float(run.off - run.on)
        if drop > best_drop:
            best, best_drop = run, drop
    return best


def _turn_in(trace: LapTrace, window: Window) -> int | None:
    """Index of the sample where the car began to turn for this corner."""
    yaw = trace.column("yaw_rate")
    if yaw is None or window.wraps:
        return None
    inside = trace.span(window.entry, window.exit)
    if len(inside) < 2:
        return None
    peak = max(inside, key=lambda i: yaw[i])
    if yaw[peak] < TURN_IN_YAW_FLOOR:
        return None
    gate = max(yaw[peak] * TURN_IN_YAW_SHARE, TURN_IN_YAW_FLOOR)
    limit = window.apex - TURN_IN_SEARCH_M
    if window.prev_apex is not None:
        limit = max(limit, window.prev_apex)
    d = trace.dist
    i = peak
    while i > 0 and yaw[i - 1] >= gate:
        i -= 1
        if d[i] < limit:
            # Still turning this far back: the corner runs on from the one
            # before it, and there is no turn-in of its own to point at.
            return None
    return i if i > 0 else None


def _balance(trace: LapTrace, window: Window, out: CornerMeasure) -> None:
    """The corner's body slip, turned to read as rotation into the corner."""
    slip = trace.column("body_slip")
    if slip is None or window.direction not in ("L", "R"):
        return
    inside = trace.span(window.entry, window.exit)
    if len(inside) < SLIP_MIN_SAMPLES:
        return
    # The channel counts the nose to the RIGHT of travel as positive, which
    # in a right-hander is into the corner and in a left-hander out of it.
    into = 1.0 if window.direction == "R" else -1.0
    values = [slip[i] * into for i in inside]
    out.slip_peak = max(values)
    out.slip_mean = sum(values) / len(values)


def measure_corner(trace: LapTrace, window: Window) -> CornerMeasure:
    out = CornerMeasure(n=window.n)
    if len(trace) < 2 or window.wraps:
        return out
    _balance(trace, window, out)
    d = trace.dist
    speed = trace.column("speed")
    brake = trace.column("brake")
    throttle = trace.column("throttle")
    gear = trace.column("gear")

    zone = _zone(trace, window)
    if zone is not None and brake is not None:
        out.brake_off = d[zone.off]
        out.brake_peak = max(brake[zone.on : zone.off + 1])
        if zone.onset_known:
            out.brake_on = d[zone.on]
        if speed is not None:
            out.brake_off_speed = speed[zone.off]
            if zone.onset_known:
                out.brake_on_speed = speed[zone.on]

    turn = _turn_in(trace, window)
    if turn is not None:
        out.turn_in = d[turn]
        if brake is not None:
            out.brake_at_turn_in = brake[turn]
            if zone is not None:
                out.trail_m = max(0.0, d[zone.off] - d[turn]) if zone.off >= turn else 0.0

    inside = trace.span(window.entry, window.exit)
    if speed is None or len(inside) == 0:
        return out
    slowest = min(inside, key=lambda i: speed[i])
    out.min_speed = speed[slowest]
    out.min_speed_dist = d[slowest]
    if gear is not None:
        out.min_speed_gear = int(gear[slowest])

    if throttle is not None:
        # From where the braking began (or the corner did) to the slowest
        # point: if the throttle never came off through all of that, the
        # corner was flat.
        lift_from = zone.on if zone is not None else inside[0]
        lifted = min(throttle[min(lift_from, slowest) : slowest + 1]) < THROTTLE_ON_PCT
        out.flat = not lifted
        if lifted:
            limit = window.exit + THROTTLE_SEARCH_M
            if window.next_apex is not None:
                limit = min(limit, window.next_apex)
            for i in range(slowest, len(d)):
                if d[i] > limit:
                    break
                if out.throttle_on is None and throttle[i] >= THROTTLE_ON_PCT:
                    out.throttle_on = d[i]
                if throttle[i] >= THROTTLE_FULL_PCT:
                    out.throttle_full = d[i]
                    break
    return out


def measure(trace: LapTrace, corner_windows: list[Window]) -> dict[int, CornerMeasure]:
    """Every corner's measurements for one lap, by corner number."""
    return {w.n: measure_corner(trace, w) for w in corner_windows}


# --- what two laps did differently -------------------------------------------


def brake_point_delta(mine: CornerMeasure | None, theirs: CornerMeasure | None) -> float | None:
    """Metres earlier (negative) or later (positive) than the reference lap.

    None unless both laps braked for the corner: a lap that took it flat has
    no braking point to be early or late against.
    """
    if mine is None or theirs is None:
        return None
    if mine.brake_on is None or theirs.brake_on is None:
        return None
    return mine.brake_on - theirs.brake_on


def brake_length(m: CornerMeasure | None) -> float | None:
    """Metres from the brake going on to its release. None for a corner taken
    without braking, and for a zone whose onset is on the lap before."""
    if m is None or m.brake_on is None or m.brake_off is None:
        return None
    return m.brake_off - m.brake_on


def min_speed_delta(mine: CornerMeasure | None, theirs: CornerMeasure | None) -> float | None:
    """km/h carried at the slowest point, relative to the reference lap."""
    if mine is None or theirs is None:
        return None
    if mine.min_speed is None or theirs.min_speed is None:
        return None
    return mine.min_speed - theirs.min_speed
