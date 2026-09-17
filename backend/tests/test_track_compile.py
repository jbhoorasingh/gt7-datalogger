"""Border ordering and compiled track geometry (#38, #40)."""

import json
import math

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
