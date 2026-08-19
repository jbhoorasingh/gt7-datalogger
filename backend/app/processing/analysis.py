"""Derived analytics: distance resampling, time deltas, deviation, fuel map.

All functions are pure and operate on the columnar lap sample dict produced by
LapProcessor (see SAMPLE_COLUMNS in laps.py).
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from typing import Any

Samples = dict[str, list[float]]

DEFAULT_STEP_M = 5.0

# Discrete per-tick columns (packed bitfields) where linear interpolation
# would fabricate values that decode to nonsense — resampled with
# nearest-neighbor instead.
NEAREST_COLUMNS = frozenset({"surface"})


def _interp(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation with edge clamping. xs must be ascending."""
    if not xs:
        return 0.0
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_left(xs, x)
    x0, x1 = xs[i - 1], xs[i]
    y0, y1 = ys[i - 1], ys[i]
    if x1 == x0:
        return y0
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def resample_by_distance(
    samples: Samples, step: float = DEFAULT_STEP_M, columns: tuple[str, ...] | None = None
) -> Samples:
    """Resample tick-based series onto a uniform distance grid."""
    dist = samples["dist"]
    if not dist:
        return {"dist": []}
    total = dist[-1]
    grid = [i * step for i in range(int(total / step) + 1)]
    out: Samples = {"dist": grid}
    cols = columns or tuple(k for k in samples if k != "dist")
    for col in cols:
        ys = samples[col]
        if col in NEAREST_COLUMNS:
            out[col] = [_nearest(dist, ys, d) for d in grid]
        else:
            out[col] = [round(_interp(dist, ys, d), 4) for d in grid]
    return out


def _nearest(xs: list[float], ys: list[float], x: float) -> float:
    """Value of the sample nearest to x. xs must be ascending."""
    if not xs:
        return 0.0
    i = bisect_left(xs, x)
    if i <= 0:
        return ys[0]
    if i >= len(xs):
        return ys[-1]
    return ys[i] if xs[i] - x < x - xs[i - 1] else ys[i - 1]


def time_delta_at(dist_m: float, t_s: float, ref: Samples) -> float | None:
    """Live gap vs a reference lap at the same distance (ms; positive = slower).

    None when the reference is empty or dist_m runs past its final sample —
    edge clamping would otherwise inflate the gap at 1 ms per ms once the
    reference lap "finishes".
    """
    if not ref["dist"] or dist_m > ref["dist"][-1]:
        return None
    return (t_s - _interp(ref["dist"], ref["t"], dist_m)) * 1000


def time_delta_series(
    lap: Samples, reference: Samples, step: float = DEFAULT_STEP_M
) -> dict[str, list[float]]:
    """Time gained/lost vs the reference lap over distance (ms; positive = slower)."""
    if not lap["dist"] or not reference["dist"]:
        return {"dist": [], "delta_ms": []}
    total = min(lap["dist"][-1], reference["dist"][-1])
    grid = [i * step for i in range(int(total / step) + 1)]
    deltas = [
        round(
            (_interp(lap["dist"], lap["t"], d) - _interp(reference["dist"], reference["t"], d))
            * 1000,
            1,
        )
        for d in grid
    ]
    return {"dist": grid, "delta_ms": deltas}


def speed_deviation(laps: list[Samples], step: float = DEFAULT_STEP_M) -> dict[str, list[float]]:
    """Median speed and standard deviation across laps, by distance.

    High deviation at a distance = inconsistent driving there.
    """
    usable = [lap for lap in laps if lap["dist"]]
    if len(usable) < 2:
        return {"dist": [], "median": [], "deviation": []}
    total = min(lap["dist"][-1] for lap in usable)
    grid = [i * step for i in range(int(total / step) + 1)]
    median: list[float] = []
    deviation: list[float] = []
    for d in grid:
        speeds = sorted(_interp(lap["dist"], lap["speed"], d) for lap in usable)
        n = len(speeds)
        mid = n // 2
        med = speeds[mid] if n % 2 else (speeds[mid - 1] + speeds[mid]) / 2
        mean = sum(speeds) / n
        var = sum((v - mean) ** 2 for v in speeds) / n
        median.append(round(med, 2))
        deviation.append(round(var**0.5, 3))
    return {"dist": grid, "median": median, "deviation": deviation}


def race_line(samples: Samples) -> dict[str, list[float]]:
    """Race line with input zones: 2=throttle, 1=coast, 0=brake per point."""
    zones: list[float] = []
    for thr, brk in zip(samples["throttle"], samples["brake"], strict=True):
        if brk >= 1:
            zones.append(0)
        elif thr >= 1:
            zones.append(2)
        else:
            zones.append(1)
    return {
        "x": samples["pos_x"],
        "z": samples["pos_z"],
        "speed": samples["speed"],
        "zone": zones,
    }


def speed_peaks_valleys(
    samples: Samples, min_gap_m: float = 100.0
) -> dict[str, list[dict[str, float]]]:
    """Local speed maxima/minima along the lap, thinned to one per min_gap_m."""
    dist, speed = samples["dist"], samples["speed"]
    peaks: list[dict[str, float]] = []
    valleys: list[dict[str, float]] = []
    w = 30  # ticks (~0.5 s) on each side
    i = w
    while i < len(speed) - w:
        window = speed[i - w : i + w + 1]
        point = {
            "dist": dist[i],
            "speed": speed[i],
            "x": samples["pos_x"][i],
            "z": samples["pos_z"][i],
        }
        if speed[i] >= max(window):
            if not peaks or dist[i] - peaks[-1]["dist"] > min_gap_m:
                peaks.append(point)
            i += w
        elif speed[i] <= min(window):
            if not valleys or dist[i] - valleys[-1]["dist"] > min_gap_m:
                valleys.append(point)
            i += w
        i += 1
    return {"peaks": peaks, "valleys": valleys}


# --- Corner detection -------------------------------------------------------

# Parameters empirically tuned against real GT7 laps (5 sessions, road courses
# + a banked oval): the goal is IDENTICAL corner counts and <30 m apex drift
# across laps of the same track — numbered corners that renumber between laps
# are useless. The hysteresis band is deliberately narrow: K_HI above ~0.0035
# loses banked/high-speed corners entirely, K_LO below ~0.002 sinks into the
# road-noise floor and bleeds adjacent corners together.
CORNER_STEP_M = 2.0  # uniform resample grid for curvature
CORNER_HALF_WIN_M = 16.0  # heading measured over ±this many meters
# The enter/stay thresholds adapt to each lap's own curvature noise (see
# _thresholds). Calibrated by sweeping candidates over real road courses,
# a banked oval, and the simulator: noisy real telemetry (jitter p85
# ~0.0004) must run the validated 0.0030/0.0022 — anything lower flips
# counts between laps; smooth data (jitter < 0.0001) safely drops to
# 0.0020/0.0013, which recovers broad-radius corners without growing
# phantoms (going lower DID grow one on the oval).
CORNER_K_LO_MIN = 0.0013  # rad/m — floor for the stay threshold
CORNER_K_LO_MAX = 0.0022  # rad/m — validated real-track value
CORNER_K_HI_RATIO = 1.54  # enter = stay * ratio ...
CORNER_K_HI_MAX = 0.0030  # ... capped at the validated real-track value
# Stay threshold = this × the p85 curvature jitter. Measured jitter: real
# GT7 laps 0.00029-0.00052 (×8 clamps them all to K_LO_MAX), smooth
# sim/parametric data 0.00005-0.00008 (lands on K_LO_MIN with 2× margin).
CORNER_NOISE_SCALE = 8.0
CORNER_END_GAP_M = 40.0  # below the stay threshold for this long ends the segment
CORNER_MERGE_GAP_M = 90.0  # same-direction arcs closer than this merge
CORNER_NOISE_ANGLE_DEG = 12.0  # arcs below this are noise, dropped pre-merge
CORNER_MIN_ANGLE_DEG = 25.0  # final significance threshold
CORNER_MAX_ANGLE_DEG = 300.0  # beyond this it's a spin, not a corner


def _wrap_angle(a: float) -> float:
    """Wrap to (-pi, pi]."""
    while a <= -math.pi:
        a += 2 * math.pi
    while a > math.pi:
        a -= 2 * math.pi
    return a


@dataclass(slots=True)
class _Arc:
    start: int  # indices into the resampled grid
    end: int  # inclusive
    sign: int
    angle: float  # total heading change, radians (signed)
    # Second index range of a start/finish-stitched corner (the post-line
    # half at the beginning of the lap); None for ordinary corners.
    wrap: tuple[int, int] | None = None


def detect_corners(samples: Samples) -> list[dict[str, float | int | str]]:
    """Auto-numbered corners from the racing line's signed curvature.

    Pipeline (each stage exists because the naive version failed on real
    laps — see docs/internals/analysis-math.md):
    resample to a uniform grid → signed curvature from wrapped chord-heading
    deltas → hysteresis segmentation that splits on direction flips → drop
    sub-noise arcs BEFORE merging (a surviving opposite blip would block
    merges on some laps only) → merge same-direction arcs across short gaps
    (a hairpin whose curvature dips mid-arc is one corner) → keep significant
    arcs → apex at the curvature-weighted centroid (min speed sits at the
    segment edge and wanders ~100 m between laps).
    """
    dist = samples.get("dist") or []
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    speed = samples.get("speed") or []
    n = min(len(dist), len(xs), len(zs), len(speed) or len(dist))
    if n < 8:
        return []
    speed = speed[:n] if speed else [0.0] * n

    # Strictly increasing distance only — duplicates/backwards points (pause
    # edges, imports) break interpolation.
    keep = [0]
    for i in range(1, n):
        if dist[i] > dist[keep[-1]]:
            keep.append(i)
    if len(keep) < 8 or dist[keep[-1]] - dist[keep[0]] < 8 * CORNER_STEP_M:
        return []
    d = [dist[i] for i in keep]
    px = [xs[i] for i in keep]
    pz = [zs[i] for i in keep]
    sp = [speed[i] for i in keep]

    # Uniform 2 m grid decouples curvature from the 60 Hz speed-dependent
    # sample spacing.
    total = d[-1] - d[0]
    m = int(total / CORNER_STEP_M) + 1
    grid = [d[0] + i * CORNER_STEP_M for i in range(m)]
    gx = [_interp(d, px, g) for g in grid]
    gz = [_interp(d, pz, g) for g in grid]
    gs = [_interp(d, sp, g) for g in grid]

    # Signed curvature: wrapped delta between the chord headings of the two
    # half-windows either side of each point, divided by the half-span.
    w = max(1, int(CORNER_HALF_WIN_M / CORNER_STEP_M))
    curv = [0.0] * m
    for i in range(w, m - w):
        dx0, dz0 = gx[i] - gx[i - w], gz[i] - gz[i - w]
        dx1, dz1 = gx[i + w] - gx[i], gz[i + w] - gz[i]
        if (dx0 == 0 and dz0 == 0) or (dx1 == 0 and dz1 == 0):
            continue
        dh = _wrap_angle(math.atan2(dz1, dx1) - math.atan2(dz0, dx0))
        curv[i] = dh / (w * CORNER_STEP_M)

    k_hi, k_lo = _thresholds(curv)
    arcs = _segment_arcs(curv, k_hi, k_lo)
    # Noise arcs are discarded BEFORE merging so they cannot block a merge.
    arcs = [a for a in arcs if abs(math.degrees(a.angle)) >= CORNER_NOISE_ANGLE_DEG]
    arcs = _merge_arcs(arcs)
    # Stitch BEFORE the significance filter: a corner split by the start line
    # must be judged on its combined angle, not per half (a 40° corner split
    # 20/20 would otherwise vanish). The spin cap also applies post-stitch.
    arcs = _stitch_wraparound(arcs, m)
    arcs = [
        a
        for a in arcs
        if CORNER_MIN_ANGLE_DEG <= abs(math.degrees(a.angle)) <= CORNER_MAX_ANGLE_DEG
    ]

    corners: list[dict[str, float | int | str]] = []
    with_apex = sorted(((a, _apex_index(a, curv)) for a in arcs), key=lambda t: t[1])
    for a, i in with_apex:
        # A stitched start/finish corner spans the lap boundary: its extent
        # wraps (entry_dist > exit_dist) and min_speed covers both halves.
        entry, exit_ = a.start, a.end
        speeds = gs[a.start : a.end + 1]
        if a.wrap is not None:
            entry, exit_ = a.start, a.wrap[1]
            speeds = speeds + gs[a.wrap[0] : a.wrap[1] + 1]
        corners.append(
            {
                "n": len(corners) + 1,
                "apex_dist": round(grid[i], 1),
                "apex_x": round(gx[i], 1),
                "apex_z": round(gz[i], 1),
                "entry_dist": round(grid[entry], 1),
                "exit_dist": round(grid[exit_], 1),
                # Positive heading delta is CCW in raw x/z, but the map (and
                # GT7's own view) renders z inverted — that's a right-hander.
                "direction": "R" if a.sign > 0 else "L",
                "min_speed": round(min(speeds), 1),
                "angle_deg": round(abs(math.degrees(a.angle)), 1),
            }
        )
    return corners


# --- authored corners (#48) ---------------------------------------------------
# Hand-labelled corners live in the track bundle and outrank detection. The
# reason is not only accuracy: detect_corners() runs PER LAP, so a lap that
# carries less speed through a shallow bend may not register it as a corner at
# all, and every corner after it renumbers. Cross-lap and cross-session
# comparison — the ground #21's report card and #22's sectors are built on —
# cannot rest on numbering that moves. Authored corners are stable by
# construction; all a lap contributes is where along ITS distance axis they
# fell.

# An apex anchor further than this from anything the lap drove is not on this
# lap: a bundle for a different layout, or a corner marked off the road.
CORNER_ANCHOR_MAX_M = 60.0
# Extent used when a corner was labelled with an apex but no entry/exit. Half
# of a fairly generous corner, clipped at the midpoint to its neighbours —
# enough for "which corner is this braking event for" without pretending the
# turn-in point is known.
CORNER_DEFAULT_HALF_M = 75.0


def _lap_path(samples: Samples) -> tuple[list[float], list[float], list[float], list[float]]:
    """Strictly-increasing (dist, x, z, speed) — duplicates break projection."""
    dist = samples.get("dist") or []
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    speed = samples.get("speed") or []
    n = min(len(dist), len(xs), len(zs))
    if n < 2:
        return [], [], [], []
    speed = speed[:n] if len(speed) >= n else [0.0] * n
    keep = [0]
    for i in range(1, n):
        if dist[i] > dist[keep[-1]]:
            keep.append(i)
    return (
        [dist[i] for i in keep], [xs[i] for i in keep],
        [zs[i] for i in keep], [speed[i] for i in keep],
    )


def _nearest_index(xs: list[float], zs: list[float], x: float, z: float) -> tuple[int, float]:
    best_i, best_d2 = 0, float("inf")
    for i in range(len(xs)):
        d2 = (xs[i] - x) ** 2 + (zs[i] - z) ** 2
        if d2 < best_d2:
            best_i, best_d2 = i, d2
    return best_i, math.sqrt(best_d2)


def project_corners(
    samples: Samples, authored: list[dict[str, Any]]
) -> list[dict[str, float | int | str]]:
    """Place a circuit's authored corners on one lap's distance axis.

    Corners are anchored to world POSITIONS, not lap distances, because
    distance depends on the racing line taken — a corner pinned at 1,240 m on
    one lap sits somewhere else on the next. So each lap resolves its own
    apex/entry/exit distances by finding where it passed the anchor, and the
    identity (number, name, direction) comes from the bundle unchanged.
    """
    d, xs, zs, speeds = _lap_path(samples)
    if len(d) < 8:
        return []
    placed: list[tuple[float, dict[str, Any], int]] = []
    for corner in authored:
        apex = corner.get("apex") or {}
        try:
            ax, az = float(apex["x"]), float(apex["z"])
        except (KeyError, TypeError, ValueError):
            continue
        i, gap = _nearest_index(xs, zs, ax, az)
        if gap > CORNER_ANCHOR_MAX_M:
            continue  # this corner is not on this lap
        placed.append((d[i], corner, i))
    if not placed:
        return []
    placed.sort(key=lambda t: t[0])

    out: list[dict[str, float | int | str]] = []
    for pos, (apex_dist, corner, apex_i) in enumerate(placed):
        far = CORNER_DEFAULT_HALF_M * 2  # no neighbour on this side to clip to
        prev_dist = placed[pos - 1][0] if pos > 0 else d[0] - far
        next_dist = placed[pos + 1][0] if pos + 1 < len(placed) else d[-1] + far
        entry = _anchor_dist(corner.get("entry"), d, xs, zs)
        exit_ = _anchor_dist(corner.get("exit"), d, xs, zs)
        if entry is None:
            entry = max(apex_dist - CORNER_DEFAULT_HALF_M, (apex_dist + prev_dist) / 2, d[0])
        if exit_ is None:
            exit_ = min(apex_dist + CORNER_DEFAULT_HALF_M, (apex_dist + next_dist) / 2, d[-1])
        lo = bisect_left(d, entry)
        hi = bisect_left(d, exit_)
        window = speeds[lo : max(hi + 1, lo + 1)] or [speeds[apex_i]]
        out.append({
            "n": int(corner.get("n", pos + 1)),
            "name": str(corner.get("name") or ""),
            # The apex is the AUTHORED position, not the nearest sample: it is
            # the same point on every lap, which is the whole point.
            "apex_dist": round(apex_dist, 1),
            "apex_x": round(float(corner["apex"]["x"]), 1),
            "apex_z": round(float(corner["apex"]["z"]), 1),
            "entry_dist": round(entry, 1),
            "exit_dist": round(exit_, 1),
            "direction": str(corner.get("direction") or _turn_direction(xs, zs, lo, hi)),
            "min_speed": round(min(window), 1),
            "angle_deg": round(_turn_angle_deg(xs, zs, lo, hi), 1),
            "authored": True,
        })
    return out


def _anchor_dist(
    point: Any, d: list[float], xs: list[float], zs: list[float]
) -> float | None:
    if not isinstance(point, dict):
        return None
    try:
        px, pz = float(point["x"]), float(point["z"])
    except (KeyError, TypeError, ValueError):
        return None
    i, gap = _nearest_index(xs, zs, px, pz)
    return d[i] if gap <= CORNER_ANCHOR_MAX_M else None


def _turn_angle_deg(xs: list[float], zs: list[float], lo: int, hi: int) -> float:
    """Heading change the lap actually made across the corner's extent.

    Descriptive only — unlike detection, nothing here decides whether the
    corner exists. A driver who straightlined a chicane gets a small number
    against a corner that is still corner 7.
    """
    if hi - lo < 4:
        return 0.0
    total = 0.0
    step = max(1, (hi - lo) // 16)
    prev: float | None = None
    for i in range(lo, hi + 1, step):
        j = min(i + step, hi)
        dx, dz = xs[j] - xs[i], zs[j] - zs[i]
        if dx == 0 and dz == 0:
            continue
        heading = math.atan2(dz, dx)
        if prev is not None:
            total += _wrap_angle(heading - prev)
        prev = heading
    return abs(math.degrees(total))


def _turn_direction(xs: list[float], zs: list[float], lo: int, hi: int) -> str:
    if hi - lo < 4:
        return "R"
    dx0, dz0 = xs[lo + 1] - xs[lo], zs[lo + 1] - zs[lo]
    dx1, dz1 = xs[hi] - xs[hi - 1], zs[hi] - zs[hi - 1]
    turn = _wrap_angle(math.atan2(dz1, dx1) - math.atan2(dz0, dx0))
    # Positive heading delta is CCW in raw x/z, but the map (and GT7's own
    # view) renders z inverted — that's a right-hander.
    return "R" if turn > 0 else "L"


def corners_for_lap(
    samples: Samples, authored: list[dict[str, Any]] | None = None
) -> list[dict[str, float | int | str]]:
    """A lap's corners: the circuit's authored ones if it has any, else detected.

    Falling back on an empty projection is deliberate — an authored set that
    places nothing on this lap means the bundle describes a different layout,
    and generic corners beat no corners.
    """
    if authored:
        projected = project_corners(samples, authored)
        if projected:
            return projected
    return detect_corners(samples)


# --- per-corner report card (#21) ---------------------------------------------

# Slack allowed when judging whether a lap covers a corner window: reference
# corner distances land on the detection grid (2 m) and lap lengths differ by
# line taken, so an exit a metre past this lap's last sample is still "driven".
GRID_TOLERANCE_M = 2.0


def corner_report(
    corners: list[dict[str, Any]], samples: Samples
) -> list[dict[str, float | int]]:
    """One lap's entry/min/exit speed and time-through for each corner.

    `corners` is the REFERENCE lap's set (authored or detected), so every lap
    in a comparison is measured through the same entry/exit windows — the same
    distance-from-start approximation the delta chart already rests on. A
    corner whose window runs past this lap's recording is omitted rather than
    clamped: the time through part of a corner reported as the whole corner
    would sort itself to the top of the report as a phantom gain.
    """
    dist = samples.get("dist") or []
    t = samples.get("t") or []
    speed = samples.get("speed") or []
    n = min(len(dist), len(t), len(speed))
    if n < 2:
        return []
    # Strictly increasing distance only — duplicates (pause edges, imports)
    # break interpolation, same as everywhere else distance is an axis.
    keep = [0]
    for i in range(1, n):
        if dist[i] > dist[keep[-1]]:
            keep.append(i)
    if len(keep) < 2:
        return []
    d = [dist[i] for i in keep]
    ts = [t[i] for i in keep]
    sp = [speed[i] for i in keep]

    out: list[dict[str, float | int]] = []
    for c in corners:
        entry, exit_ = float(c["entry_dist"]), float(c["exit_dist"])
        if entry <= exit_:
            if entry < d[0] - GRID_TOLERANCE_M or exit_ > d[-1] + GRID_TOLERANCE_M:
                continue  # this lap never drove the full window
            time_s = _interp(d, ts, exit_) - _interp(d, ts, entry)
            lo, hi = bisect_left(d, entry), bisect_left(d, exit_)
            window = sp[lo:hi] or [_interp(d, sp, (entry + exit_) / 2)]
        else:
            # A start/finish-stitched corner wraps the lap boundary: its time
            # is the tail of this lap plus the head of it — so the lap must
            # cover BOTH: a short lap that never reached the entry would
            # otherwise clamp to its last sample and report the head alone,
            # a phantom gain sorted straight to the top of the card.
            if exit_ > d[-1] + GRID_TOLERANCE_M or entry > d[-1] + GRID_TOLERANCE_M:
                continue
            time_s = (ts[-1] - _interp(d, ts, entry)) + (_interp(d, ts, exit_) - ts[0])
            lo = bisect_left(d, entry)
            hi = bisect_left(d, exit_)
            window = (sp[lo:] + sp[:hi]) or [sp[0]]
        out.append({
            "n": int(c["n"]),
            "entry_speed": round(_interp(d, sp, entry), 1),
            "min_speed": round(min(window), 1),
            "exit_speed": round(_interp(d, sp, exit_), 1),
            "time_ms": round(time_s * 1000, 1),
        })
    return out


def _thresholds(curv: list[float]) -> tuple[float, float]:
    """Hysteresis thresholds anchored to this lap's curvature noise floor.

    The floor is the high end of the frame-to-frame curvature jitter — real
    GT7 telemetry wobbles around zero on straights, smooth data barely at
    all. The stay threshold must clear that jitter or adjacent corners bleed
    together; but fixing it at the real-track value silently drops the
    broad-radius corners of clean low-curvature tracks.
    """
    jitter = sorted(abs(curv[i] - curv[i - 1]) for i in range(1, len(curv)))
    noise = jitter[int(len(jitter) * 0.85)] if jitter else 0.0
    k_lo = min(CORNER_K_LO_MAX, max(CORNER_K_LO_MIN, noise * CORNER_NOISE_SCALE))
    k_hi = min(k_lo * CORNER_K_HI_RATIO, CORNER_K_HI_MAX)
    return k_hi, k_lo


def _segment_arcs(curv: list[float], k_hi: float, k_lo: float) -> list[_Arc]:
    """Hysteresis segmentation of the curvature series, split on sign flips."""
    end_gap = int(CORNER_END_GAP_M / CORNER_STEP_M)
    arcs: list[_Arc] = []
    i = 0
    m = len(curv)
    while i < m:
        if abs(curv[i]) <= k_hi:
            i += 1
            continue
        sign = 1 if curv[i] > 0 else -1
        start = i
        last_active = i
        angle = 0.0
        while i < m:
            k = curv[i]
            if k * sign > 0 and abs(k) >= k_lo:
                last_active = i
                angle += k * CORNER_STEP_M
                i += 1
            elif k * -sign > k_hi:
                break  # strong opposite curvature: an S — new corner
            elif i - last_active >= end_gap:
                break  # faded out for long enough
            else:
                i += 1
        arcs.append(_Arc(start=start, end=last_active, sign=sign, angle=angle))
    return arcs


def _merge_arcs(arcs: list[_Arc]) -> list[_Arc]:
    """Merge same-direction arcs across short gaps (double-apex complexes)."""
    gap = int(CORNER_MERGE_GAP_M / CORNER_STEP_M)
    out: list[_Arc] = []
    for a in arcs:
        prev = out[-1] if out else None
        if prev is not None and prev.sign == a.sign and a.start - prev.end <= gap:
            prev.end = a.end
            prev.angle += a.angle
        else:
            out.append(_Arc(a.start, a.end, a.sign, a.angle))
    return out


def _stitch_wraparound(arcs: list[_Arc], m: int) -> list[_Arc]:
    """A lap that starts mid-corner shows one physical corner split across
    the start/finish line; merge the two halves into one corner.

    Each half may sit at most half the merge gap from its lap edge, so the
    combined tolerance matches the mid-lap merge distance. The surviving arc
    is the pre-line half; `wrap` records the post-line half so the corner's
    extent (entry > exit signals the wrap), min_speed, and apex consider
    both halves.
    """
    edge = int(CORNER_MERGE_GAP_M / 2 / CORNER_STEP_M)
    if len(arcs) < 2:
        return arcs
    first, last = arcs[0], arcs[-1]
    if (
        first.sign == last.sign
        and first.start <= edge
        and m - 1 - last.end <= edge
    ):
        last.angle += first.angle
        last.wrap = (first.start, first.end)
        return arcs[1:]
    return arcs


def _apex_index(a: _Arc, curv: list[float]) -> int:
    """Curvature-weighted centroid: stable within ~25 m across laps, where
    the min-speed point wanders with braking for the NEXT corner.

    For a start/finish-stitched corner the centroid comes from whichever
    half turns more — averaging indices across the lap seam would land the
    apex mid-lap, nowhere near the corner.
    """

    def centroid(lo: int, hi: int) -> tuple[int, float]:
        total = 0.0
        weighted = 0.0
        for i in range(lo, hi + 1):
            w = abs(curv[i])
            total += w
            weighted += w * i
        idx = round(weighted / total) if total > 0 else (lo + hi) // 2
        return idx, total

    idx0, t0 = centroid(a.start, a.end)
    if a.wrap is None:
        return idx0
    idx1, t1 = centroid(*a.wrap)
    return idx0 if t0 >= t1 else idx1


# --- Accelerometer calibration (#16) -----------------------------------------
#
# GT7 broadcasts `sway`/`heave`/`surge` (packet B) with no documented unit and
# no documented sign convention, and the simulator source cannot prove what a
# real console sends — which is exactly why #16 said "validate before building
# UI". So the app validates them against physics it already records, per lap:
#
#   lateral       a = v * omega      omega = signed heading rate from the path
#   longitudinal  a = dv/dt          from the speed trace
#
# Both references are in m/s^2 and both are signed, so a least-squares slope
# through the origin recovers the broadcast channel's UNIT (slope ~1 means it
# is m/s^2, ~0.102 means it is already g) and its SIGN at the same time. What
# comes back is a multiplier to g, so a g-g diagram reads correctly whichever
# convention the console turns out to use — and says so when the fit is too
# weak to trust.

GRAVITY = 9.80665

# A lap has thousands of ticks; a fit resting on a handful of them is noise.
ACC_MIN_SAMPLES = 150
# Share of the channel's magnitude the scaled reference has to explain. This
# is R² about ZERO, not about the mean: the model is forced through the origin
# (see _fit_through_origin), and a mean-centred correlation collapses on
# exactly the cleanest case there is — a long constant-rate braking zone,
# where both series are near-constant and Pearson r becomes numerical noise.
ACC_MIN_R2 = 0.75
# Only fit where the reference itself is large enough to have a meaningful
# ratio: near zero the quotient is dominated by jitter in both series.
ACC_MIN_REF = 1.5  # m/s^2
ACC_MIN_SPEED = 8.0  # m/s — below this, heading from position is noise
# A slope this small would mean the channel is ~flat against the reference;
# inverting it produces an absurd scale rather than a calibration.
ACC_MIN_SLOPE = 1e-3


def _fit_through_origin(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """(slope, R² about zero) for y = slope * x, forced through the origin.

    The intercept is fixed at zero on purpose: zero reference acceleration
    must mean zero broadcast acceleration, and a free intercept would happily
    absorb a systematic offset that is the very thing worth seeing. The
    goodness measure is taken about zero for the same reason — how much of the
    channel's own magnitude the scaled reference accounts for. It is 1 for a
    perfect fit and goes negative for a channel the reference does not
    describe at all.
    """
    if len(xs) < 2:
        return 0.0, 0.0
    sxx = sum(x * x for x in xs)
    syy = sum(y * y for y in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0, 0.0
    slope = sum(x * y for x, y in zip(xs, ys, strict=True)) / sxx
    sse = sum((y - slope * x) ** 2 for x, y in zip(xs, ys, strict=True))
    return slope, 1.0 - sse / syy


# Half-window for the path heading, in METRES of track rather than ticks.
# Positions are stored rounded to a centimetre, so heading resolution is
# 0.01 / chord: over the ~1 m a tick covers at speed that is 0.01 rad, which
# swamps the 0.002 rad/tick a real corner turns, and differencing consecutive
# ticks yields a staircase of quantisation noise rather than a yaw rate. Over
# 8 m the same rounding is 0.001 rad, two orders below what a 46 m radius
# turns across that distance. Distance-based because the tick spacing itself
# varies with speed by a factor of five.
HEADING_SPAN_M = 8.0


def _heading_rate(samples: Samples) -> list[float]:
    """Signed yaw rate (rad/s) from the driven path, per tick.

    The stored `yaw_rate` column is an ABSOLUTE value, so it cannot settle
    which way `sway` counts positive. Positive here is the same convention
    corner detection uses: a positive heading delta in raw x/z, which the map
    (z inverted, as GT7 draws it) renders as a right-hander.

    The chord-heading-difference scheme is the one `detect_corners` uses, for
    the same reason: it measures the turn over a fixed length of road instead
    of a fixed number of samples.
    """
    t = samples.get("t") or []
    xs = samples.get("pos_x") or []
    zs = samples.get("pos_z") or []
    dist = samples.get("dist") or []
    n = min(len(t), len(xs), len(zs), len(dist))
    out = [0.0] * n
    if n < 5:
        return out
    for i in range(n):
        lo = bisect_left(dist, dist[i] - HEADING_SPAN_M, 0, i)
        hi = bisect_left(dist, dist[i] + HEADING_SPAN_M, i, n)
        hi = min(hi, n - 1)
        if hi - i < 1 or i - lo < 1:
            continue
        before = math.atan2(zs[i] - zs[lo], xs[i] - xs[lo])
        after = math.atan2(zs[hi] - zs[i], xs[hi] - xs[i])
        # The two chords represent the heading at their own midpoints in time.
        dt = (t[hi] - t[lo]) / 2
        if dt > 0:
            out[i] = _wrap_angle(after - before) / dt
    return out


def _axis(
    raw: list[float], reference: list[float], mask: list[bool]
) -> dict[str, Any]:
    """Calibrate one accelerometer axis against a physical reference."""
    xs = [reference[i] for i, ok in enumerate(mask) if ok]
    ys = [raw[i] for i, ok in enumerate(mask) if ok]
    slope, r2 = _fit_through_origin(xs, ys)
    ok = len(xs) >= ACC_MIN_SAMPLES and r2 >= ACC_MIN_R2 and abs(slope) >= ACC_MIN_SLOPE
    return {
        "slope": round(slope, 5),  # broadcast units per m/s^2
        "r2": round(r2, 4),
        "samples": len(xs),
        "fitted": ok,
        # What a consumer multiplies the raw channel by to get g. Carries the
        # sign, so a channel that counts the other way comes out upright.
        "g_per_unit": round(1.0 / (slope * GRAVITY), 6) if ok else round(1.0 / GRAVITY, 6),
    }


def accel_calibration(samples: Samples) -> dict[str, Any]:
    """Unit + sign calibration for a lap's broadcast accelerometer channels.

    Returns `available: False` for a recording made on packet A (no
    accelerometer at all). When a fit is too weak the axis falls back to
    assuming m/s^2 and reports `fitted: False`, so the UI can show the
    diagram and still say the calibration is unproven.
    """
    lat_raw = samples.get("acc_lat") or []
    long_raw = samples.get("acc_long") or []
    t = samples.get("t") or []
    speed = samples.get("speed") or []
    heading = _heading_rate(samples)
    n = min(len(t), len(speed), len(lat_raw), len(long_raw), len(heading))
    if n < ACC_MIN_SAMPLES:
        return {"available": False}

    v = [speed[i] / 3.6 for i in range(n)]  # km/h -> m/s
    lat_ref = [v[i] * heading[i] for i in range(n)]
    long_ref = [0.0] * n
    for i in range(1, n - 1):
        dt = t[i + 1] - t[i - 1]
        if dt > 0:
            long_ref[i] = (v[i + 1] - v[i - 1]) / dt

    moving = [v[i] >= ACC_MIN_SPEED for i in range(n)]
    lat = _axis(
        lat_raw[:n], lat_ref, [moving[i] and abs(lat_ref[i]) >= ACC_MIN_REF for i in range(n)]
    )
    lon = _axis(
        long_raw[:n], long_ref, [moving[i] and abs(long_ref[i]) >= ACC_MIN_REF for i in range(n)]
    )
    return {
        "available": True,
        "lateral": lat,
        "longitudinal": lon,
        # One line a human can read: what the broadcast unit appears to be.
        "unit": _unit_guess(lat, lon),
    }


def _unit_guess(lat: dict[str, Any], lon: dict[str, Any]) -> str:
    slopes = [abs(a["slope"]) for a in (lat, lon) if a["fitted"]]
    if not slopes:
        return "unverified"
    mean = sum(slopes) / len(slopes)
    if 0.7 <= mean <= 1.4:
        return "m/s^2"
    if 0.07 <= mean <= 0.14:
        return "g"
    return f"1 unit = {round(1 / mean, 3)} m/s^2"


def gg_extremes(samples: Samples, calibration: dict[str, Any]) -> dict[str, float]:
    """Peak g actually used, in each direction — the corners of the envelope."""
    lat_k = calibration.get("lateral", {}).get("g_per_unit", 1 / GRAVITY)
    long_k = calibration.get("longitudinal", {}).get("g_per_unit", 1 / GRAVITY)
    lat = [v * lat_k for v in samples.get("acc_lat") or []]
    lon = [v * long_k for v in samples.get("acc_long") or []]
    # Clamped at zero: a direction the lap never went in used no g at all, and
    # an oval driven one way round would otherwise report a negative peak for
    # the side it never turned to.
    return {
        "lat_right": round(max(0.0, max(lat, default=0.0)), 3),
        "lat_left": round(max(0.0, -min(lat, default=0.0)), 3),
        "accel": round(max(0.0, max(lon, default=0.0)), 3),
        "braking": round(max(0.0, -min(lon, default=0.0)), 3),
    }


# --- Fuel map ---------------------------------------------------------------

# GT7's fuel map setting (1..6 in some cars, modeled here as -5..+5 relative
# to current) changes fuel consumption ~10% per step; leaner mixture also
# costs lap time. These factors approximate observed in-game behavior.
FUEL_CONSUMPTION_PER_STEP = 0.10
LAP_TIME_COST_PER_STEP_MS = 250


@dataclass(slots=True)
class FuelMapRow:
    setting: int
    fuel_per_lap: float
    laps_remaining: float
    time_remaining_ms: int
    lap_time_delta_ms: int


def fuel_map(
    fuel_level: float, fuel_per_lap: float, lap_time_ms: int, settings_range: int = 5
) -> list[FuelMapRow]:
    """Laps/time remaining for each relative fuel-map setting.

    Positive settings burn more fuel (richer, faster); negative save fuel.
    """
    rows: list[FuelMapRow] = []
    if fuel_per_lap <= 0 or lap_time_ms <= 0:
        return rows
    for setting in range(-settings_range, settings_range + 1):
        consumption = fuel_per_lap * (1 + FUEL_CONSUMPTION_PER_STEP * setting)
        lap_delta = -LAP_TIME_COST_PER_STEP_MS * setting
        adjusted_lap_ms = lap_time_ms + lap_delta
        laps_remaining = fuel_level / consumption if consumption > 0 else float("inf")
        rows.append(
            FuelMapRow(
                setting=setting,
                fuel_per_lap=round(consumption, 3),
                laps_remaining=round(laps_remaining, 2),
                time_remaining_ms=int(laps_remaining * adjusted_lap_ms),
                lap_time_delta_ms=lap_delta,
            )
        )
    return rows
