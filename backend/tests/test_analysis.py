"""Derived calculations: resampling, deltas, deviation, fuel map, race line."""

import pytest

from app.processing import analysis
from app.processing.laps import new_sample_store


def make_lap(total_dist: float, speed: float, n: int = 100) -> dict[str, list[float]]:
    """Constant-speed lap: distance grows linearly, t = dist / speed."""
    s = new_sample_store()
    for i in range(n):
        d = total_dist * i / (n - 1)
        s["t"].append(d / speed)
        s["dist"].append(d)
        s["speed"].append(speed * 3.6)
        s["throttle"].append(100.0)
        s["brake"].append(0.0)
        s["coast"].append(0.0)
        s["gear"].append(4.0)
        s["rpm"].append(6000.0)
        s["boost"].append(0.0)
        s["tire_slip"].append(1.0)
        s["yaw_rate"].append(0.1)
        s["pos_x"].append(d)
        s["pos_z"].append(0.0)
        s["body_height"].append(90.0)
        s["fuel"].append(100.0 - d / 1000)
    return s


def test_resample_by_distance() -> None:
    lap = make_lap(1000.0, 50.0)
    out = analysis.resample_by_distance(lap, step=100.0, columns=("speed", "t"))
    assert out["dist"] == [i * 100.0 for i in range(11)]
    assert all(v == pytest.approx(180.0) for v in out["speed"])
    assert out["t"][5] == pytest.approx(500 / 50, abs=0.01)


def test_time_delta_series_slower_lap_positive() -> None:
    fast = make_lap(1000.0, 50.0)
    slow = make_lap(1000.0, 40.0)
    delta = analysis.time_delta_series(slow, fast, step=100.0)
    # At 1000 m: slow t=25 s, fast t=20 s -> +5000 ms
    assert delta["delta_ms"][-1] == pytest.approx(5000.0, abs=10)
    assert all(d >= 0 for d in delta["delta_ms"])


def test_time_delta_reference_vs_itself_is_zero() -> None:
    lap = make_lap(1000.0, 50.0)
    delta = analysis.time_delta_series(lap, lap, step=100.0)
    assert all(abs(d) < 1e-6 for d in delta["delta_ms"])


def test_speed_deviation() -> None:
    laps = [make_lap(1000.0, 50.0), make_lap(1000.0, 50.0), make_lap(1000.0, 60.0)]
    out = analysis.speed_deviation(laps, step=100.0)
    assert out["median"][0] == pytest.approx(180.0)  # median of 180,180,216
    assert all(d > 0 for d in out["deviation"])
    # Identical laps -> zero deviation
    same = analysis.speed_deviation([make_lap(1000.0, 50.0)] * 3, step=100.0)
    assert all(d == pytest.approx(0.0) for d in same["deviation"])


def test_speed_deviation_needs_two_laps() -> None:
    out = analysis.speed_deviation([make_lap(1000.0, 50.0)])
    assert out["dist"] == []


def test_race_line_zones() -> None:
    lap = make_lap(300.0, 50.0, n=3)
    lap["throttle"] = [100.0, 0.0, 0.0]
    lap["brake"] = [0.0, 100.0, 0.0]
    line = analysis.race_line(lap)
    assert line["zone"] == [2, 0, 1]  # throttle, brake, coast


def test_fuel_map() -> None:
    rows = analysis.fuel_map(fuel_level=50.0, fuel_per_lap=2.0, lap_time_ms=90_000)
    assert len(rows) == 11
    neutral = next(r for r in rows if r.setting == 0)
    assert neutral.fuel_per_lap == pytest.approx(2.0)
    assert neutral.laps_remaining == pytest.approx(25.0)
    assert neutral.time_remaining_ms == 25 * 90_000
    lean = next(r for r in rows if r.setting == -5)
    rich = next(r for r in rows if r.setting == 5)
    # Leaner -> less fuel per lap, more laps remaining, slower laps
    assert lean.fuel_per_lap < neutral.fuel_per_lap < rich.fuel_per_lap
    assert lean.laps_remaining > neutral.laps_remaining > rich.laps_remaining
    assert lean.lap_time_delta_ms > 0 > rich.lap_time_delta_ms


def test_fuel_map_invalid_inputs() -> None:
    assert analysis.fuel_map(50.0, 0.0, 90_000) == []
    assert analysis.fuel_map(50.0, 2.0, 0) == []


def test_interp_edges() -> None:
    xs, ys = [0.0, 10.0], [0.0, 100.0]
    assert analysis._interp(xs, ys, -5.0) == 0.0
    assert analysis._interp(xs, ys, 15.0) == 100.0
    assert analysis._interp(xs, ys, 5.0) == pytest.approx(50.0)


# --- corner detection --------------------------------------------------------


def track_lap(segments, step: float = 2.0, speed_kmh: float = 180.0) -> dict:
    """Build a lap from ('straight', length_m) / ('arc', radius_m, angle_deg)
    segments. Positive angle is CCW in raw x/z coordinates — which the map
    renders (z inverted) as a RIGHT-hander, so detect_corners labels it "R"."""
    import math

    s = new_sample_store()
    x, z, heading, dist = 0.0, 0.0, 0.0, 0.0

    def emit() -> None:
        s["dist"].append(round(dist, 2))
        s["pos_x"].append(round(x, 3))
        s["pos_z"].append(round(z, 3))
        s["speed"].append(speed_kmh)
        s["t"].append(dist / (speed_kmh / 3.6))

    emit()
    for seg in segments:
        if seg[0] == "straight":
            length = seg[1]
            n = max(1, int(length / step))
            for _ in range(n):
                x += step * math.cos(heading)
                z += step * math.sin(heading)
                dist += step
                emit()
        else:
            _, radius, angle_deg = seg
            arc_len = abs(math.radians(angle_deg)) * radius
            n = max(2, int(arc_len / step))
            dh = math.radians(angle_deg) / n
            for _ in range(n):
                heading += dh
                x += step * math.cos(heading)
                z += step * math.sin(heading)
                dist += step
                emit()
    return s


def test_corners_straight_line_has_none() -> None:
    lap = track_lap([("straight", 2000)])
    assert analysis.detect_corners(lap) == []


def test_corners_single_90_degree_turn() -> None:
    lap = track_lap([("straight", 400), ("arc", 100, 90), ("straight", 400)])
    corners = analysis.detect_corners(lap)
    assert len(corners) == 1
    c = corners[0]
    assert c["n"] == 1
    assert c["direction"] == "R"  # positive heading delta renders as a right-hander
    assert 60 <= c["angle_deg"] <= 120
    # apex roughly mid-arc: arc spans 400..557
    assert 400 <= c["apex_dist"] <= 560


def test_corners_s_section_is_two_corners() -> None:
    lap = track_lap(
        [("straight", 300), ("arc", 90, 80), ("arc", 90, -80), ("straight", 300)]
    )
    corners = analysis.detect_corners(lap)
    assert [c["direction"] for c in corners] == ["R", "L"]
    assert [c["n"] for c in corners] == [1, 2]


def test_corners_hairpin_with_mid_dip_is_one_corner() -> None:
    # 180° hairpin whose curvature briefly relaxes mid-arc (double-apex):
    # two 85° arcs joined by a ~42 m near-straight interlude (real GT7
    # double-apex complexes contain 50-80 m low-curvature interludes).
    lap = track_lap(
        [
            ("straight", 400),
            ("arc", 60, 85),
            ("arc", 1200, 2),  # low-curvature interlude, same direction
            ("arc", 60, 85),
            ("straight", 400),
        ]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 1
    assert corners[0]["angle_deg"] > 120


def test_corners_numbered_in_track_order() -> None:
    lap = track_lap(
        [
            ("straight", 300),
            ("arc", 80, 90),
            ("straight", 500),
            ("arc", 70, -120),
            ("straight", 500),
            ("arc", 90, 60),
            ("straight", 300),
        ]
    )
    corners = analysis.detect_corners(lap)
    assert [c["n"] for c in corners] == [1, 2, 3]
    dists = [c["apex_dist"] for c in corners]
    assert dists == sorted(dists)
    assert [c["direction"] for c in corners] == ["R", "L", "R"]


def test_corners_shallow_kink_ignored() -> None:
    # 10° bend at huge radius (|k|≈0.0017, below even the relaxed clean-data
    # enter threshold): a kink, not a corner
    lap = track_lap([("straight", 500), ("arc", 600, 10), ("straight", 500)])
    assert analysis.detect_corners(lap) == []


def test_corners_spin_arc_dropped() -> None:
    # A 420° loop (spin) must not be numbered
    lap = track_lap(
        [("straight", 400), ("arc", 80, 90), ("straight", 200), ("arc", 20, 420),
         ("straight", 200), ("arc", 80, -90), ("straight", 400)]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 2
    assert all(c["angle_deg"] <= 300 for c in corners)


def test_corners_wraparound_stitch() -> None:
    # Lap starts mid-corner: the same physical corner appears at the very
    # start and very end; it must be counted once.
    lap = track_lap(
        [
            ("arc", 80, 45),  # second half of the start/finish corner
            ("straight", 600),
            ("arc", 80, 90),
            ("straight", 600),
            ("arc", 80, 45),  # first half of the start/finish corner
        ]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 2
    # The stitched corner's extent wraps the lap boundary: entry near the
    # lap end, exit near the lap start (entry > exit signals the wrap).
    wrap = max(corners, key=lambda c: c["entry_dist"])
    assert wrap["entry_dist"] > wrap["exit_dist"]
    assert wrap["exit_dist"] < 100.0


def test_corners_wraparound_min_speed_covers_both_halves() -> None:
    lap = track_lap(
        [
            ("arc", 80, 45),
            ("straight", 600),
            ("arc", 80, 90),
            ("straight", 600),
            ("arc", 80, 45),
        ]
    )
    # Speed dip inside the POST-line half (start of the lap)
    for i, d in enumerate(lap["dist"]):
        if d <= 40:
            lap["speed"][i] = 95.0
    corners = analysis.detect_corners(lap)
    wrap = max(corners, key=lambda c: c["entry_dist"])
    assert wrap["min_speed"] == pytest.approx(95.0, abs=1.0)


def test_corners_degenerate_inputs() -> None:
    assert analysis.detect_corners({}) == []
    assert analysis.detect_corners({"dist": [], "pos_x": [], "pos_z": [], "speed": []}) == []
    short = track_lap([("straight", 10)])
    assert analysis.detect_corners(short) == []
    # non-monotonic dist must not crash
    lap = track_lap([("straight", 300), ("arc", 80, 90), ("straight", 300)])
    lap["dist"][50] = lap["dist"][49]  # duplicate
    lap["dist"][120] = lap["dist"][119] - 5  # backwards
    assert isinstance(analysis.detect_corners(lap), list)
    # unequal lengths must not crash
    lap2 = track_lap([("straight", 300), ("arc", 80, 90), ("straight", 300)])
    lap2["speed"] = lap2["speed"][:-30]
    assert isinstance(analysis.detect_corners(lap2), list)


def test_corners_min_speed_reported() -> None:
    lap = track_lap([("straight", 400), ("arc", 100, 90), ("straight", 400)])
    # dip the speed inside the corner
    for i, d in enumerate(lap["dist"]):
        if 420 <= d <= 540:
            lap["speed"][i] = 90.0
    corners = analysis.detect_corners(lap)
    assert len(corners) == 1
    assert corners[0]["min_speed"] == pytest.approx(90.0, abs=1.0)


def test_corners_merge_across_long_interlude() -> None:
    """A ~73 m low-curvature interlude (real double-apex complexes contain
    50-80 m ones) is long enough that segmentation splits the arc — the
    merge stage must recombine it. Guards _merge_arcs against regressions."""
    lap = track_lap(
        [
            ("straight", 400),
            ("arc", 60, 85),
            ("arc", 1200, 3.5),  # ~73 m near-straight, same direction
            ("arc", 60, 85),
            ("straight", 400),
        ]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 1
    assert corners[0]["angle_deg"] > 120


def test_corners_split_by_start_line_survives_significance_filter() -> None:
    """A 40° corner split 20/20 across the start/finish line: each half is
    below the 25° significance threshold, so stitching must happen first."""
    lap = track_lap(
        [
            ("arc", 150, 20),  # second half of the start/finish corner
            ("straight", 700),
            ("arc", 80, 90),
            ("straight", 700),
            ("arc", 150, 20),  # first half of the start/finish corner
        ]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 2
    # The ±16 m curvature window is blind at the lap edges, so the stitched
    # angle reads low (~27° of the true 40°) — surviving the 25° filter is
    # the behavior under test.
    angles = sorted(c["angle_deg"] for c in corners)
    assert 25.0 <= angles[0] <= 45.0


def test_corners_two_distinct_corners_near_line_not_stitched() -> None:
    """Two separate corners ~120 m apart across the start line must stay two
    (the stitch tolerance matches the mid-lap 90 m merge gap, not double)."""
    lap = track_lap(
        [
            ("straight", 60),
            ("arc", 80, 60),
            ("straight", 700),
            ("arc", 80, 90),
            ("straight", 700),
            ("arc", 80, 60),
            ("straight", 60),
        ]
    )
    corners = analysis.detect_corners(lap)
    assert len(corners) == 3


# --- authored corners (#48) ---------------------------------------------------


def _authored(*apexes, names=()):
    return [
        {"n": i + 1, "name": names[i] if i < len(names) else "",
         "direction": None, "apex": {"x": x, "z": z},
         "entry": None, "exit": None, "note": ""}
        for i, (x, z) in enumerate(apexes)
    ]


def test_authored_corners_are_placed_on_the_lap_that_drove_them() -> None:
    """Corners are anchored to POSITIONS, not lap distances: distance depends
    on the racing line taken, so each lap has to resolve its own."""
    lap = track_lap([("straight", 200), ("arc", 60, 90), ("straight", 300)])
    detected = analysis.detect_corners(lap)
    assert len(detected) == 1
    apex = (float(detected[0]["apex_x"]), float(detected[0]["apex_z"]))

    corners = analysis.project_corners(lap, _authored(apex, names=("Rettifilo",)))
    assert len(corners) == 1
    c = corners[0]
    assert c["name"] == "Rettifilo" and c["authored"] is True
    assert c["apex_dist"] == pytest.approx(float(detected[0]["apex_dist"]), abs=15.0)
    assert c["entry_dist"] < c["apex_dist"] < c["exit_dist"]
    assert c["direction"] == "R"


def test_authored_numbering_survives_a_lap_detection_disagrees_about() -> None:
    """The real problem with detect_corners(): it runs PER LAP, off the racing
    LINE. A driver who straightlines an S takes the same tarmac on a shallower
    arc, detection stops calling it a corner, and every corner after it
    renumbers — so "turn 4" in one lap's report is "turn 2" in the next, and
    cross-lap comparison (#21, #22) is built on sand."""
    committed = track_lap([("straight", 150), ("arc", 70, 80), ("straight", 200),
                           ("arc", 200, 32), ("arc", 200, -32), ("straight", 200),
                           ("arc", 60, 100), ("straight", 150)])
    straightlined = track_lap([("straight", 150), ("arc", 70, 80), ("straight", 200),
                               ("arc", 400, 16), ("arc", 400, -16), ("straight", 200),
                               ("arc", 60, 100), ("straight", 150)])
    detected_a = analysis.detect_corners(committed)
    detected_b = analysis.detect_corners(straightlined)
    assert len(detected_a) == 4 and len(detected_b) == 2
    # The last corner is the same one on both laps, and detection calls it 4
    # on one and 2 on the other. That is the bug, in one assertion.
    assert detected_a[-1]["n"] != detected_b[-1]["n"]
    assert detected_a[-1]["apex_z"] == pytest.approx(detected_b[-1]["apex_z"], abs=15)

    authored = _authored(*[(float(c["apex_x"]), float(c["apex_z"])) for c in detected_a])
    on_a = analysis.corners_for_lap(committed, authored)
    on_b = analysis.corners_for_lap(straightlined, authored)
    assert [c["n"] for c in on_a] == [c["n"] for c in on_b] == [1, 2, 3, 4]
    # Same numbers, same tarmac — the apex is the authored position on every
    # lap, not wherever this lap's curvature peaked.
    assert [(c["apex_x"], c["apex_z"]) for c in on_a] == [
        (c["apex_x"], c["apex_z"]) for c in on_b
    ]
    assert all(c["authored"] for c in on_b)
    # ...while each lap still resolves its own distances, because the line
    # taken decides how far along the lap the corner falls.
    assert on_a[-1]["apex_dist"] != on_b[-1]["apex_dist"]


def test_authored_corners_from_another_layout_fall_back_to_detection() -> None:
    """A bundle whose corners land nowhere near this lap describes a different
    circuit; generic corners beat none."""
    lap = track_lap([("straight", 200), ("arc", 60, 90), ("straight", 300)])
    corners = analysis.corners_for_lap(lap, _authored((50_000.0, 50_000.0)))
    assert corners == analysis.detect_corners(lap)


def test_authored_entry_and_exit_anchors_are_used_when_marked() -> None:
    lap = track_lap([("straight", 200), ("arc", 60, 90), ("straight", 300)])
    i_entry, i_apex = 60, 105
    authored = [{
        "n": 1, "name": "", "direction": "L", "note": "",
        "apex": {"x": lap["pos_x"][i_apex], "z": lap["pos_z"][i_apex]},
        "entry": {"x": lap["pos_x"][i_entry], "z": lap["pos_z"][i_entry]},
        "exit": None,
    }]
    c = analysis.project_corners(lap, authored)[0]
    assert c["entry_dist"] == pytest.approx(lap["dist"][i_entry], abs=2.0)
    assert c["direction"] == "L"  # the label wins over what the lap looks like


# --- per-corner report card (#21) ---------------------------------------------


def _window(n: int, entry: float, exit_: float) -> dict:
    return {"n": n, "entry_dist": entry, "exit_dist": exit_}


def test_corner_report_constant_speed() -> None:
    lap = make_lap(1000.0, 50.0)  # 50 m/s = 180 km/h
    report = analysis.corner_report([_window(1, 100.0, 300.0)], lap)
    assert len(report) == 1
    c = report[0]
    assert c["n"] == 1
    assert c["entry_speed"] == pytest.approx(180.0)
    assert c["min_speed"] == pytest.approx(180.0)
    assert c["exit_speed"] == pytest.approx(180.0)
    assert c["time_ms"] == pytest.approx(4000.0, abs=10)  # 200 m at 50 m/s


def test_corner_report_slower_lap_loses_time() -> None:
    fast = make_lap(1000.0, 50.0)
    slow = make_lap(1000.0, 40.0)
    corners = [_window(1, 100.0, 300.0)]
    lost = (
        analysis.corner_report(corners, slow)[0]["time_ms"]
        - analysis.corner_report(corners, fast)[0]["time_ms"]
    )
    # 200 m: 5 s at 40 m/s vs 4 s at 50 m/s
    assert lost == pytest.approx(1000.0, abs=10)


def test_corner_report_min_speed_is_the_dip() -> None:
    lap = make_lap(1000.0, 50.0)
    for i, d in enumerate(lap["dist"]):
        if 180 <= d <= 220:
            lap["speed"][i] = 120.0
    report = analysis.corner_report([_window(1, 100.0, 300.0)], lap)
    assert report[0]["min_speed"] == pytest.approx(120.0)
    assert report[0]["entry_speed"] == pytest.approx(180.0)


def test_corner_report_omits_windows_the_lap_never_drove() -> None:
    lap = make_lap(1000.0, 50.0)
    report = analysis.corner_report(
        [_window(1, 100.0, 300.0), _window(2, 900.0, 1200.0)], lap
    )
    assert [c["n"] for c in report] == [1]


def test_corner_report_wraparound_window() -> None:
    lap = make_lap(1000.0, 50.0)
    report = analysis.corner_report([_window(5, 900.0, 100.0)], lap)
    assert len(report) == 1
    # tail (100 m) + head (100 m) at 50 m/s
    assert report[0]["time_ms"] == pytest.approx(4000.0, abs=10)


def test_corner_report_wraparound_needs_both_halves() -> None:
    """A short lap that never reached the wrapped corner's entry must omit it:
    clamping would report the head alone as the whole corner — a phantom gain
    sorted straight to the top of the card."""
    short = make_lap(600.0, 50.0)  # ends 300 m before the entry
    assert analysis.corner_report([_window(5, 900.0, 100.0)], short) == []


def test_corner_report_degenerate_inputs() -> None:
    assert analysis.corner_report([_window(1, 0.0, 100.0)], {}) == []
    assert analysis.corner_report([], make_lap(1000.0, 50.0)) == []
    lap = make_lap(1000.0, 50.0)
    lap["dist"] = [0.0] * len(lap["dist"])  # no usable distance axis
    assert analysis.corner_report([_window(1, 0.0, 100.0)], lap) == []
