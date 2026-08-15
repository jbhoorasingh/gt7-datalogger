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


def _ring(radius=100.0, width=10.0, skip=(), y_inner=5.0):
    """A circular circuit, one border cell per metre on each side.

    Travel is counterclockwise: at angle a the heading is (-sin a, cos a),
    whose right-normal (hz, -hx) points outward — so the RIGHT border is the
    outer circle and the LEFT the inner one, `width` metres apart. `skip` is
    a set of (side, step) pairs to leave unsurveyed.
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
            edges.append(_edge(r * math.cos(a), r * math.sin(a), hx, hz, "R"))
    return edges


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
    runs, quads, paired = track_compile.centerline_and_road(left, right)
    assert len(runs) >= 1
    samples = [v for r in runs for v in r]
    assert len(samples) > 100
    for x, z, y, w in samples:
        assert abs(math.hypot(x, z) - 100.0) < 1.0  # midway between 95 and 105
        assert abs(w - 10.0) < 1.0
        assert y == 5.0  # only the inner border knows its elevation
    assert len(quads) > 100
    assert paired > 0.95


def test_unpaired_stretches_produce_no_centerline() -> None:
    # the right border is missing entirely: nothing to pair against
    steps = int(2 * math.pi * 100)
    left, right = _sides(_ring(skip={("R", i) for i in range(steps)}))
    runs, quads, paired = track_compile.centerline_and_road(left, right)
    assert runs == []
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
