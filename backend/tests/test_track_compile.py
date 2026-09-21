"""Border ordering and compiled track geometry (#38, #40)."""

import json
import math
from pathlib import Path

from app.processing import track_bundle, track_compile

SOURCE = "abc123abc123"


def _edge(x, z, hx, hz, side, kind="auto", y=None):
    return {
        "x": x, "z": z, "y": y, "hx": hx, "hz": hz,
        "side": side, "kind": kind, "votes": {kind: {SOURCE: [1, 1]}},
        "run": 1, "tw": 1.6,
    }


def _ring(radius=100.0, width=10.0, skip=(), y_inner=5.0, y_outer=None):
    """A circular circuit, one border cell per metre on each side.

    Travel is counterclockwise: at angle a the heading is (-sin a, cos a),
    whose right-normal (hz, -hx) points outward — so the RIGHT border is the
    outer circle and the LEFT the inner one, `width` metres apart. `skip` is
    a set of (side, step) pairs to leave unsurveyed. By default only the
    inner border knows its elevation; `y_outer` raises the outer one, which
    is what a banked oval looks like.
    """
    edges = []
    steps = int(2 * math.pi * radius)  # ~1 m along the centerline
    for i in range(steps):
        a = 2 * math.pi * i / steps
        hx, hz = -math.sin(a), math.cos(a)
        if ("L", i) not in skip:
            r = radius - width / 2
            edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, "L",
                               y=y_inner))
        if ("R", i) not in skip:
            r = radius + width / 2
            edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, "R",
                               y=y_outer))
    return edges


def _straight(length=200.0, width=10.0, y=0.0, angle=0.0):
    """A straight road through the origin, `length` m long, travelled at
    `angle` radians from +x: one border cell per metre on each side, every
    cell at elevation `y`. The right-normal is (hz, -hx), as the survey lays
    it, so R sits on that side of the centreline and L on the other."""
    hx, hz = math.cos(angle), math.sin(angle)
    rx, rz = hz, -hx
    edges = []
    for i in range(int(length)):
        t = i - length / 2
        cx, cz = hx * t, hz * t
        edges.append(_edge(cx - rx * width / 2, cz - rz * width / 2, hx, hz, "L", y=y))
        edges.append(_edge(cx + rx * width / 2, cz + rz * width / 2, hx, hz, "R", y=y))
    return edges


SHALLOW_CROSSING = math.radians(20)


def _crossover(angle=SHALLOW_CROSSING, deck=8.0):
    """Two straight roads crossing at the origin: one at ground level, one
    `deck` metres up, meeting at `angle`. Shallow by default so that, without
    elevation, the walk would happily hop from one to the other (its heading
    gate allows 60°) — which is exactly what #96 is about."""
    return _straight(y=0.0) + _straight(y=deck, angle=angle)


def _document(edges, finish=(), track="Test Circuit"):
    return {
        "format": track_bundle.BUNDLE_FORMAT,
        "version": track_bundle.BUNDLE_VERSION,
        "meta": {
            "track": track, "runs": 2, "source_runs": {SOURCE: 2},
            "updated_at": "2026-08-01T00:00:00+00:00", "official": None,
        },
        "edges": edges,
        "finish_crossings": list(finish),
        "corners": [],
        "sections": [],
    }


def _sides(edges):
    left = track_compile.SideAssembly([e for e in edges if e["side"] == "L"])
    right = track_compile.SideAssembly([e for e in edges if e["side"] == "R"])
    return left, right


# --- ordering ----------------------------------------------------------------


def test_a_fully_surveyed_ring_orders_into_one_closed_loop_per_side() -> None:
    left, right = _sides(_ring())
    assert len(left.chains) == 1 and left.closed
    assert len(right.chains) == 1 and right.closed
    for side, radius in ((left, 95.0), (right, 105.0)):
        cov = side.coverage()
        assert cov["pct"] == 100.0
        assert cov["gap_m"] == 0.0
        assert abs(cov["surveyed_m"] - 2 * math.pi * radius) < 10
        assert side.gap_spans() == []


def test_a_survey_hole_is_bridged_but_flagged_and_counted() -> None:
    # 30 unsurveyed metres of the inner border: the ordering bridges it (the
    # continuation is clean), but as a GAP — undrawn, and against coverage.
    hole = {("L", i) for i in range(100, 130)}
    left, _ = _sides(_ring(skip=hole))
    assert len(left.chains) == 1 and left.closed
    cov = left.coverage()
    assert cov["pct"] < 100.0
    assert cov["gap_m"] > 20.0
    assert len(left.gap_spans()) == 1
    # the drawn polylines stop at the hole rather than crossing it
    spans = left.polylines()
    assert len(spans) == 2 or (len(spans) == 1 and left.gap_spans())


def test_fragments_stay_fragments_rather_than_joining_wrongly() -> None:
    # Two opposite 90° arcs: the ~140 m between them is far beyond any
    # plausible stitch, so they must stay two open chains.
    steps = int(2 * math.pi * 100)
    keep = set(range(0, steps // 4)) | set(range(steps // 2, 3 * steps // 4))
    skip = {("L", i) for i in range(steps) if i not in keep}
    left, _ = _sides(_ring(skip=skip))
    assert len(left.chains) == 2
    assert not left.closed


def test_borders_a_road_apart_never_chain_together() -> None:
    left, right = _sides(_ring(width=10.0))
    # no left cell ended up in a right chain or vice versa: chain lengths per
    # side account for (almost) all of that side's own cells
    for side in (left, right):
        chained = sum(len(c) for c in side.chains)
        assert chained >= 0.9 * len(side.pts)


# --- centerline and road -----------------------------------------------------


def test_centerline_runs_midway_and_carries_width_and_elevation() -> None:
    left, right = _sides(_ring(radius=100.0, width=10.0, y_inner=5.0))
    runs, quads, _levels, paired = track_compile.centerline_and_road(left, right)
    assert len(runs) >= 1
    samples = [v for r in runs for v in r]
    assert len(samples) > 100
    for x, z, y, w in samples:
        assert abs(math.hypot(x, z) - 100.0) < 1.0  # midway between 95 and 105
        assert abs(w - 10.0) < 1.0
        assert y == 5.0  # only the inner border knows its elevation
    assert len(quads) > 100
    assert paired > 0.95


def test_a_closed_loop_is_sampled_across_its_seam() -> None:
    # The chain has an arbitrary seed; a surveyed closure must not leave an
    # artificial hole there (the edge judge would read it as off-road).
    left, _ = _sides(_ring())
    samples = track_compile._resample(left, track_compile.CL_STEP_M)
    real = [s for s in samples if s is not None]
    breaks = sum(1 for s in samples if s is None)
    assert breaks == 1  # only the trailing chain-boundary marker
    circumference = 2 * math.pi * 95.0
    assert len(real) >= circumference / track_compile.CL_STEP_M - 1


def test_a_flagged_gap_breaks_the_sample_stream() -> None:
    # Samples on opposite sides of a small gap can sit closer than 2×step;
    # the break marker is what stops a quad from bridging unsurveyed ground.
    # Two holes: whichever ends up as the chain's seam, the other is internal
    # and must produce a mid-stream break.
    holes = {("L", i) for i in range(100, 108)} | {("L", i) for i in range(400, 408)}
    left, right = _sides(_ring(skip=holes))
    samples = track_compile._resample(left, track_compile.CL_STEP_M)
    assert sum(1 for s in samples if s is None) >= 2  # gap break + trailing
    _, quads, _, _ = track_compile.centerline_and_road(left, right)
    # no quad spans a hole: every quad's left edge stays under 2×step
    for q in quads:
        assert math.hypot(q[2] - q[0], q[3] - q[1]) <= 2 * track_compile.CL_STEP_M


def test_unpaired_stretches_produce_no_centerline() -> None:
    # the right border is missing entirely: nothing to pair against
    steps = int(2 * math.pi * 100)
    left, right = _sides(_ring(skip={("R", i) for i in range(steps)}))
    runs, quads, levels, paired = track_compile.centerline_and_road(left, right)
    assert runs == []
    assert levels == []
    assert quads == []
    assert paired == 0.0


# --- the compiled document ---------------------------------------------------


def test_compile_bundle_carries_geometry_coverage_and_provenance() -> None:
    doc = _document(_ring(), finish=[{"x": 95.0, "z": 0.0, "hx": 0.0,
                                      "hz": 1.0, "lap": 2}])
    compiled = track_compile.compile_bundle(doc)
    assert compiled["format"] == track_compile.COMPILED_FORMAT
    assert compiled["version"] == track_compile.COMPILED_VERSION
    assert compiled["track"] == "Test Circuit"
    assert compiled["slug"] == "test-circuit"
    assert compiled["borders"]["L"] and compiled["borders"]["R"]
    assert compiled["road"]
    assert compiled["finish"] is not None
    assert compiled["coverage"]["L"]["closed"]
    assert compiled["coverage"]["road_pct"] > 95.0
    src = compiled["source"]
    assert src["points"] == len(doc["edges"])
    assert src["runs"] == 2 and src["sources"] == 1
    assert src["app_version"]


def test_walls_are_ordered_with_the_borders_but_drawn_apart() -> None:
    edges = _ring()
    for e in edges:
        if e["side"] == "R":
            e["kind"] = "wall"
            e["votes"] = {"wall": {SOURCE: [1, 1]}}
    compiled = track_compile.compile_bundle(_document(edges))
    # the wall side still closes and still bounds the road...
    assert compiled["coverage"]["R"]["closed"]
    assert compiled["road"]
    # ...and is additionally drawable as its own layer
    assert len(compiled["walls"]) == sum(1 for e in edges if e["side"] == "R")


# --- persistence and auto-recompile ------------------------------------------


def test_for_track_persists_and_recompiles_when_the_bundle_changes(tmp_path) -> None:
    track = "Test Circuit"
    path = track_bundle.bundle_path(tmp_path, track)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_document(_ring())), encoding="utf-8")

    track_compile._CACHE.clear()
    first = track_compile.for_track(tmp_path, track)
    assert first is not None and first["road"]
    stored = track_compile._compiled_path(tmp_path, "test-circuit")
    assert stored.exists()

    # unchanged bundle: the persisted compile is reused (same compiled_at)
    track_compile._CACHE.clear()
    again = track_compile.for_track(tmp_path, track)
    assert again is not None
    assert again["compiled_at"] == first["compiled_at"]

    # a bundle write invalidates it
    doc = _document(_ring(), track=track)
    doc["meta"]["runs"] = 3
    path.write_text(json.dumps(doc), encoding="utf-8")
    track_compile._CACHE.clear()
    recompiled = track_compile.for_track(tmp_path, track)
    assert recompiled is not None
    assert recompiled["source"]["runs"] == 3


def test_a_circuit_without_a_bundle_compiles_to_none(tmp_path) -> None:
    assert track_compile.for_track(tmp_path, "Nowhere") is None
    assert track_compile.for_track(tmp_path, "") is None


# --- a circuit that crosses over itself (#96) --------------------------------


def _chain_levels(side: track_compile.SideAssembly) -> list[set[float]]:
    return [{side.pts[i]["y"] for i in chain} for chain in side.chains]


def test_a_crossover_keeps_each_level_on_its_own_chain() -> None:
    """Under the bridge the two levels' cells interleave in plan; the walk
    must not step from the deck onto the road beneath it."""
    left, right = _sides(_crossover())
    for side in (left, right):
        assert len(side.chains) == 2
        for levels in _chain_levels(side):
            assert len(levels) == 1, "a chain that changes level walked across the crossover"
        assert not side.closed


def test_without_elevation_the_walk_cannot_tell_the_levels_apart() -> None:
    """What the gate is for: the same crossing with pre-v3 records (no y)
    walks as one road, because nothing says it is two."""
    edges = _crossover()
    for e in edges:
        e["y"] = None
    left, _ = _sides(edges)
    assert len(left.chains) < 2 or any(len(c) > 200 for c in left.chains)


def test_a_steep_hill_still_chains_and_stitches() -> None:
    # 30 % grade along a straight — steeper than any surveyed circuit — with
    # a 30 m survey hole in it: the level gate must not read the climb as a
    # change of level, so the hole is still bridged as one chain.
    edges = []
    for i in range(200):
        if 100 <= i < 130:
            continue
        edges.append(_edge(float(i), 5.0, 1.0, 0.0, "L", y=0.3 * i))
        edges.append(_edge(float(i), -5.0, 1.0, 0.0, "R", y=0.3 * i))
    left, right = _sides(edges)
    assert len(left.chains) == 1 and len(right.chains) == 1
    assert len(left.gap_spans()) == 1


def test_crossover_quads_never_span_both_levels() -> None:
    """Pairing the left border across to the right prefers its own level:
    a quad whose corners sit on both roads would be a slice of nothing."""
    compiled = track_compile.compile_bundle(_document(_crossover()))
    levels = compiled["road_y"]
    assert len(levels) == len(compiled["road"])
    assert all(lv is not None for lv in levels)
    assert all(hi - lo < 1.0 for lo, hi in levels)
    assert {lo for lo, _ in levels} == {0.0, 8.0}  # both roads compiled
    assert compiled["coverage"]["road_pct"] > 90.0


def test_a_banked_road_is_one_level_with_an_envelope() -> None:
    # Daytona: the outer border 5.5 m above the inner across an 8-12 m road.
    # Still one road — the pairing must find the far border although it is
    # further away in elevation than the levels of a crossover are.
    compiled = track_compile.compile_bundle(_document(_ring(y_inner=0.0, y_outer=5.5)))
    assert compiled["coverage"]["road_pct"] > 95.0
    for lo, hi in compiled["road_y"]:
        assert lo == 0.0 and hi == 5.5


def test_quads_without_elevation_on_both_borders_have_no_level() -> None:
    compiled = track_compile.compile_bundle(_document(_ring()))  # only L knows y
    assert compiled["road_y"] and all(lv is None for lv in compiled["road_y"])


# --- border smoothing ---------------------------------------------------------

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "smooth_vectors.json").read_text(encoding="utf-8")
)


def _corner(radius, turn_deg, step, *, snap=True, straight=60.0):
    """A straight, a corner of `radius` through `turn_deg`, a straight. With
    `snap`, as a survey records it: on the 1 m grid, no cell twice."""
    true = []
    s = -straight
    while s < 0:
        true.append((s, 0.0))
        s += step
    turn = math.radians(turn_deg)
    a = 0.0
    while a < turn * radius:
        true.append((radius * math.sin(a / radius), radius * (1 - math.cos(a / radius))))
        a += step
    ex, ez = radius * math.sin(turn), radius * (1 - math.cos(turn))
    s = 0.0
    while s < straight:
        true.append((ex + s * math.cos(turn), ez + s * math.sin(turn)))
        s += step
    if not snap:
        return true
    cells = [(float(round(x)), float(round(z))) for x, z in true]
    return [cells[0]] + [b for a, b in zip(cells, cells[1:], strict=False) if b != a]


def _worst_error(truth, points):
    return max(min(math.hypot(x - a, z - b) for a, b in truth) for x, z in points)


def _kinks(points, degrees=35.0, leg=3.0):
    """Turns sharper than `degrees` with a leg shorter than `leg` metres: a
    nick in the border, which no road has."""
    count = 0
    for a, b, c in zip(points, points[1:], points[2:], strict=False):
        v1, v2 = (b[0] - a[0], b[1] - a[1]), (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 < 1e-6 or n2 < 1e-6:
            continue
        turn = math.degrees(math.atan2(
            v1[0] * v2[1] - v1[1] * v2[0], v1[0] * v2[0] + v1[1] * v2[1]
        ))
        if abs(turn) > degrees and min(n1, n2) < leg:
            count += 1
    return count


def test_smoothing_reproduces_the_vectors_the_editor_is_held_to() -> None:
    # The track editor carries this routine in JavaScript and is tested
    # against the same file. A change that moves these numbers is a change to
    # both: regenerate with tests/data/make_smooth_vectors.py and take the
    # file across.
    params = VECTORS["parameters"]
    assert params == {
        "iterations": track_compile.SMOOTH_ITERATIONS,
        "lambda": track_compile.SMOOTH_LAMBDA,
        "mu": track_compile.SMOOTH_MU,
        "cap_m": track_compile.SMOOTH_CAP_M,
    }
    for name, case in VECTORS["cases"].items():
        got = track_compile.smooth_run(
            [tuple(p) for p in case["points"]], closed=case["closed"]
        )
        for (x, z), (ex, ez) in zip(got, case["expected"], strict=True):
            assert math.isclose(x, ex, abs_tol=1e-6), name
            assert math.isclose(z, ez, abs_tol=1e-6), name


def test_smoothing_does_not_cut_a_sharp_corner() -> None:
    # The whole reason this is Taubin and not an average: measured against
    # corners whose true border is known, a ±8 m moving average is 2.0 m
    # inside a 3 m kerb and 1.4 m inside a 10 m hairpin. track_limits judges a
    # car off the road a metre past the edge, so that is a wrong verdict on
    # exactly the corners where verdicts matter.
    for radius, turn in ((3, 90), (6, 70), (10, 180), (20, 180), (80, 60)):
        truth = _corner(radius, turn, 0.1, snap=False)
        for step in (1.0, 2.0):
            recorded = _corner(radius, turn, step)
            smoothed = track_compile.smooth_run(recorded)
            error = _worst_error(truth, smoothed)
            assert error <= 0.4, (radius, step, error)
            # And never meaningfully worse than the grid it started from.
            assert error <= _worst_error(truth, recorded) + 0.1, (radius, step)


def test_smoothing_removes_the_step_where_the_record_kind_changes() -> None:
    stepped = [(float(i), 0.0 if i < 30 else 1.0) for i in range(60)]
    assert _kinks(stepped) == 2  # a metre sideways in a metre: 45° in, 45° out
    smoothed = track_compile.smooth_run(stepped)
    assert _kinks(smoothed) == 0
    # The two kinds still disagree about where the edge is, and far from the
    # step each stretch stays where its own records put it.
    assert abs(smoothed[5][1]) < 0.01 and abs(smoothed[-6][1] - 1.0) < 0.01


def test_smoothing_keeps_a_runs_ends_and_honours_the_cap() -> None:
    spiked = [(float(i), 0.0) for i in range(20)]
    spiked[10] = (10.0, 3.0)
    smoothed = track_compile.smooth_run(spiked)
    assert smoothed[0] == spiked[0] and smoothed[-1] == spiked[-1]
    moves = [math.hypot(a[0] - b[0], a[1] - b[1])
             for a, b in zip(spiked, smoothed, strict=True)]
    assert max(moves) <= track_compile.SMOOTH_CAP_M + 1e-9
    assert math.isclose(moves[10], track_compile.SMOOTH_CAP_M, abs_tol=1e-9)


def test_smoothing_does_not_creep_records_along_an_unevenly_surveyed_straight() -> None:
    # One record a metre, then one every five: an unweighted average would
    # slide them towards even spacing and spend the cap on moving nothing.
    straight = [(float(x), 2.0) for x in (0, 1, 2, 3, 8, 13, 14, 15, 20, 21)]
    for before, after in zip(straight, track_compile.smooth_run(straight), strict=True):
        assert math.isclose(before[0], after[0], abs_tol=1e-9)
        assert math.isclose(before[1], after[1], abs_tol=1e-9)


def test_smoothing_does_not_shrink_a_closed_loop() -> None:
    n = 360
    ring = [((100 + 0.5 * (-1) ** i) * math.cos(2 * math.pi * i / n),
             (100 + 0.5 * (-1) ** i) * math.sin(2 * math.pi * i / n)) for i in range(n)]

    def area(p):
        return abs(sum(a[0] * b[1] - b[0] * a[1]
                       for a, b in zip(p, p[1:] + p[:1], strict=True))) / 2

    smoothed = track_compile.smooth_run(ring, closed=True)
    assert abs(area(smoothed) - area(ring)) / area(ring) < 0.001
    radii = [math.hypot(x, z) for x, z in smoothed]
    assert max(radii) - min(radii) < 0.2  # the ±0.5 m zigzag is gone


def test_switching_smoothing_off_compiles_the_evidence_exactly_as_recorded() -> None:
    # The administrator's switch. Off must mean off: the same document the
    # compiler produced before it could smooth at all.
    doc = _document(_ring(skip={("R", i) for i in range(40, 70)}))
    off = track_compile.compile_bundle(doc, smooth=False)
    assert off["smoothing"] is None
    raw = {(round(e["x"], 2), round(e["z"], 2)) for e in doc["edges"]}
    for side in ("L", "R"):
        for run in off["borders"][side]:
            assert all((v[0], v[1]) in raw for v in run)

    on = track_compile.compile_bundle(doc, smooth=True)
    assert on["smoothing"] == {
        "method": "taubin", "iterations": 10, "lambda": 0.5, "mu": -0.53, "cap_m": 0.75,
    }
    # And what is on when nobody says is what the constant says.
    default = track_compile.compile_bundle(doc)
    assert (default["smoothing"] is not None) == track_compile.SMOOTH_BORDERS


def test_smoothing_never_reaches_across_a_gap_or_rewrites_the_bundle() -> None:
    edges = _ring(skip={("R", i) for i in range(40, 70)})
    doc = _document(edges)
    before = json.dumps(doc, sort_keys=True)
    off = track_compile.compile_bundle(doc, smooth=False)
    on = track_compile.compile_bundle(doc, smooth=True)
    # The records are evidence; smoothing is done to copies of them.
    assert json.dumps(doc, sort_keys=True) == before
    # Both ends of the hole are where the survey left them, so the gap is the
    # same gap and the coverage it costs is the same coverage.
    assert on["gaps"] == off["gaps"] and len(on["gaps"]["R"]) == 1
    assert on["coverage"]["R"]["gap_m"] == off["coverage"]["R"]["gap_m"]
    assert on["coverage"]["L"]["closed"] and on["coverage"]["L"]["pct"] == 100.0


def test_a_stepped_border_compiles_without_its_nicks() -> None:
    # A straight whose right-hand records sit a metre further out for a
    # stretch, as `straddle` records do beside `edge` ones.
    edges = _straight(length=200.0)
    for e in edges:
        if e["side"] == "R" and 80 <= e["x"] + 100 < 120:
            e["z"] -= 1.0
    doc = _document(edges)

    def nicks(compiled):
        return sum(_kinks([(v[0], v[1]) for v in run]) for run in compiled["borders"]["R"])

    assert nicks(track_compile.compile_bundle(doc, smooth=False)) > 0
    assert nicks(track_compile.compile_bundle(doc, smooth=True)) == 0


def test_a_closed_loop_is_smoothed_round_its_seam() -> None:
    left = track_compile.SideAssembly(
        [e for e in _ring() if e["side"] == "L"], smooth=True
    )
    assert left.closed and left.smoothed
    runs, ring = left._runs(0)
    assert ring and len(runs) == 1
    # Every vertex of a perfect circle is already where its neighbours say,
    # the seed of the walk included: nothing has an end to be pinned at.
    radii = [math.hypot(left.pts[i]["x"], left.pts[i]["z"]) for i in left.chains[0]]
    assert max(radii) - min(radii) < 0.01
