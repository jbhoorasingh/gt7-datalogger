"""Sector boundaries and sector times (#22): authored on the circuit, thirds
of the lap without them, and every lap of a session split at the same places."""

import math

import pytest

from app.processing import sectors, track_bundle
from app.service import TelemetryService
from app.telemetry.packet import build_packet, parse_packet
from tests.circle_track import RADIUS
from tests.test_api import ON_TRACK
from tests.test_lap_analysis import circle_lap
from tests.test_track_manager import _bundle_with_corners, _foreign_bundle, client  # noqa: F401

LAP_M = RADIUS * 2 * math.pi

# The API tests drive a small circle: 120 packets a lap at 40 m/s, a tick
# apart, so each lap is 80 m round and two seconds long by its own clock.
TICKS = 120
SPEED = 40.0
RING_R = TICKS * SPEED / 60 / (2 * math.pi)
LAP_TIME_MS = round(TICKS / 60 * 1000)


async def drive_circle(service: TelemetryService, laps: int = 2, join_tick: int = 0) -> None:
    """Laps with a path and a clock, which the sector maths needs and the
    straight-line laps of tests.test_api.drive_laps do not have. `join_tick`
    starts the recording that far into the first lap, as a logger switched on
    mid-lap would."""
    for lap in range(1, laps + 1):
        for tick in range(join_tick if lap == 1 else 0, TICKS):
            theta = 2 * math.pi * tick / TICKS
            await service._on_packet(parse_packet(build_packet(
                packet_id=lap * 1000 + tick,
                position=(RING_R * math.cos(theta), 0.0, RING_R * math.sin(theta)),
                current_lap=lap,
                last_lap_time_ms=LAP_TIME_MS if lap > 1 else -1,
                speed_mps=SPEED,
                throttle=255,
                flags=ON_TRACK,
                car_id=7,
            )))
    await service._on_packet(parse_packet(build_packet(
        packet_id=(laps + 1) * 1000,
        position=(RING_R, 0.0, 0.0),
        current_lap=laps + 1,
        last_lap_time_ms=LAP_TIME_MS,
        speed_mps=SPEED,
        flags=ON_TRACK,
        car_id=7,
    )))


def _on_circle(deg: float) -> dict[str, float]:
    return {"x": RADIUS * math.cos(math.radians(deg)), "z": RADIUS * math.sin(math.radians(deg))}


AUTHORED = [
    {"n": 1, "name": "Esses", "end": _on_circle(90.0)},
    {"n": 2, "name": "", "end": _on_circle(300.0)},
]


# --- boundaries ----------------------------------------------------------------


def test_without_authored_sectors_the_reference_lap_supplies_thirds() -> None:
    points, own = sectors.boundary_points([], circle_lap())
    assert own is False
    assert [p["n"] for p in points] == [1, 2]
    # A third and two thirds of the way round the circle, as PLACES.
    for point, deg in zip(points, (120.0, 240.0), strict=True):
        expected = _on_circle(deg)
        assert math.hypot(point["x"] - expected["x"], point["z"] - expected["z"]) < 3.0


def test_authored_sectors_outrank_thirds() -> None:
    points, own = sectors.boundary_points(AUTHORED, circle_lap())
    assert own is True
    assert [(p["n"], p["name"]) for p in points] == [(1, "Esses"), (2, "")]
    assert points[0]["x"] == pytest.approx(AUTHORED[0]["end"]["x"])


def test_nothing_to_split_by_without_a_reference_lap() -> None:
    assert sectors.boundary_points([], None) == ([], False)
    # Authored boundaries need no reference lap at all.
    assert sectors.boundary_points(AUTHORED, None)[1] is True


def test_boundaries_resolve_to_each_laps_own_distance() -> None:
    points, _ = sectors.boundary_points(AUTHORED, None)
    placed = sectors.project(circle_lap(), points)
    assert placed is not None
    assert placed[0] == pytest.approx(RADIUS * math.radians(90.0), abs=1.0)
    assert placed[1] == pytest.approx(RADIUS * math.radians(300.0), abs=1.0)


def test_a_lap_that_never_passed_a_boundary_has_no_sectors() -> None:
    far = [{"n": 1, "name": "", "end": {"x": 5000.0, "z": 0.0}}]
    points, _ = sectors.boundary_points(far, None)
    assert sectors.project(circle_lap(), points) is None
    assert sectors.sectors_for_lap(circle_lap(), points, True) == []
    assert sectors.splits_ms(circle_lap(), points, 60_000) is None


def test_boundaries_out_of_order_on_the_lap_are_refused() -> None:
    backwards = [{"n": 1, "name": "", "end": _on_circle(300.0)},
                 {"n": 2, "name": "", "end": _on_circle(90.0)}]
    points, _ = sectors.boundary_points(backwards, None)
    assert sectors.project(circle_lap(), points) is None


def test_sector_windows_tile_the_lap() -> None:
    points, own = sectors.boundary_points(AUTHORED, None)
    windows = sectors.sectors_for_lap(circle_lap(), points, own)
    assert [w["n"] for w in windows] == [1, 2, 3]
    assert [w["name"] for w in windows] == ["Esses", "", ""]
    assert windows[0]["start_dist"] == 0.0
    for a, b in zip(windows, windows[1:], strict=False):
        assert a["end_dist"] == b["start_dist"]
    assert windows[-1]["end_dist"] == pytest.approx(LAP_M, abs=LAP_M / 100)
    assert all(w["authored"] is True for w in windows)


# --- splits ----------------------------------------------------------------------


def test_splits_sum_to_the_lap_time() -> None:
    lap = circle_lap()
    time_ms = round(lap["t"][-1] * 1000) + 16  # the line is a tick past the last sample
    points, _ = sectors.boundary_points([], lap)
    splits = sectors.splits_ms(lap, points, time_ms)
    assert splits is not None and len(splits) == 3
    assert sum(splits) == pytest.approx(time_ms, abs=0.5)
    # The circle lap's clock is linear in distance, so thirds of the lap are
    # thirds of the time.
    for split in splits:
        assert split == pytest.approx(time_ms / 3, rel=0.02)


def test_the_clock_is_read_from_the_line_not_the_first_sample() -> None:
    """`t` is anchored on the line and the first sample sits half a frame
    past it. A split measured from that sample instead would hand the gap
    to the last sector on every lap — so the gap has to show up in S1."""
    lap = circle_lap()
    late = {**lap, "t": [v + 0.25 for v in lap["t"]]}  # the first sample a quarter second in
    time_ms = round(late["t"][-1] * 1000) + 16
    points, _ = sectors.boundary_points([], lap)
    plain = sectors.splits_ms(lap, points, time_ms)
    shifted = sectors.splits_ms(late, points, time_ms)
    assert plain is not None and shifted is not None
    assert shifted[0] == pytest.approx(plain[0] + 250, abs=0.5)
    assert shifted[1] == pytest.approx(plain[1], abs=0.5)
    assert shifted[-1] == pytest.approx(plain[-1] - 250, abs=0.5)


def test_no_clock_no_lap_time_no_splits() -> None:
    lap = circle_lap()
    points, _ = sectors.boundary_points([], lap)
    without_clock = {k: v for k, v in lap.items() if k != "t"}
    assert sectors.splits_ms(without_clock, points, 60_000) is None
    assert sectors.splits_ms(lap, points, 0) is None
    # A lap time shorter than the clock at the first boundary is not this
    # lap's time — a partial lap's — and cannot be split.
    assert sectors.splits_ms(lap, points, 100) is None


def test_a_clock_that_missed_part_of_the_lap_has_no_splits() -> None:
    """GT7's lap time covers the whole lap; the lap's own clock only what was
    recorded. Where they disagree the missing time cannot be placed, and
    reading the boundaries anyway would hand all of it to the last sector."""
    lap = circle_lap()
    time_ms = round(lap["t"][-1] * 1000) + 16
    points, _ = sectors.boundary_points([], lap)
    # Joined a sixth of the way round, inside S1: the clock and the distance
    # start where the recording did, and the lap still passes both boundaries.
    k = len(lap["t"]) // 6
    joined = {key: values[k:] for key, values in lap.items()}
    joined["t"] = [v - lap["t"][k] for v in joined["t"]]
    joined["dist"] = [v - lap["dist"][k] for v in joined["dist"]]
    assert sectors.project(joined, points) is not None
    assert sectors.splits_ms(joined, points, time_ms) is None
    # A stop the sampler skipped: GT7's clock ran on through it, ours froze.
    assert sectors.splits_ms(lap, points, time_ms + 30_000) is None
    # A clock that ran past the lap time is no better.
    assert sectors.splits_ms(lap, points, time_ms - 5_000) is None


def test_a_clock_within_honest_drift_still_splits() -> None:
    """The salvage window: integration drift well under half a second over
    a lap is the same lap, and its splits still sum to the lap time."""
    lap = circle_lap()
    time_ms = round(lap["t"][-1] * 1000) + 16 + 300
    points, _ = sectors.boundary_points([], lap)
    splits = sectors.splits_ms(lap, points, time_ms)
    assert splits is not None
    assert sum(splits) == pytest.approx(time_ms, abs=0.5)


# --- over the API ----------------------------------------------------------------


async def test_the_comparison_carries_thirds_on_the_reference(client) -> None:  # noqa: F811
    c, service, _tmp = client
    await drive_circle(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    ref = laps[0]["id"]
    body = (await c.get(f"/api/analysis/compare?laps={ref}&ref={ref}")).json()
    windows = body["laps"][str(ref)]["sectors"]
    assert [w["n"] for w in windows] == [1, 2, 3]
    assert all(w["authored"] is False for w in windows)
    assert windows[0]["start_dist"] == 0.0
    assert windows[2]["end_dist"] == pytest.approx(
        body["laps"][str(ref)]["series"]["dist"][-1], abs=0.1
    )


async def test_every_lap_of_a_session_is_split_at_the_same_places(client) -> None:  # noqa: F811
    c, service, _tmp = client
    await drive_circle(service, laps=3)
    session = (await c.get("/api/sessions")).json()[0]
    body = (await c.get(f"/api/analysis/sectors?session_id={session['id']}")).json()
    assert body["source"] == "thirds"
    assert [s["n"] for s in body["sectors"]] == [1, 2, 3]
    assert [lap["number"] for lap in body["laps"]] == [1, 2, 3]
    assert sum(1 for lap in body["laps"] if lap["reference"]) == 1
    for lap in body["laps"]:
        assert lap["splits_ms"] is not None and len(lap["splits_ms"]) == 3
        assert sum(lap["splits_ms"]) == pytest.approx(lap["time_ms"], abs=0.5)


async def test_a_lap_joined_part_way_round_has_no_splits(client) -> None:  # noqa: F811
    """GT7 reports the whole lap's time for a lap the logger joined part way
    round, but the lap's clock started where the recording did, not on the
    line, so the splits would put the stretch it missed in the last sector."""
    c, service, _tmp = client
    # Just short of the first boundary, a third of the way round: the lap
    # still passes both boundaries in order and nothing else stops it.
    await drive_circle(service, laps=3, join_tick=TICKS // 3 - 1)
    session = (await c.get("/api/sessions")).json()[0]
    body = (await c.get(f"/api/analysis/sectors?session_id={session['id']}")).json()
    by_number = {lap["number"]: lap for lap in body["laps"]}
    assert by_number[1]["time_ms"] == LAP_TIME_MS
    assert by_number[1]["splits_ms"] is None
    for n in (2, 3):
        assert sum(by_number[n]["splits_ms"]) == pytest.approx(LAP_TIME_MS, abs=0.5)


async def test_authored_sectors_split_the_session(client) -> None:  # noqa: F811
    c, service, tmp = client
    await drive_circle(service, laps=2)
    laps = (await c.get("/api/laps")).json()
    samples = (await service.repo.get_laps_samples([laps[0]["id"]]))[laps[0]["id"]]
    await service.repo.set_session_track(laps[0]["session_id"], "Ring")
    _bundle_with_corners(tmp)
    n = len(samples["pos_x"])
    resp = await c.put("/api/track-bundles/ring/corners", json={"sectors": [
        {"name": "Esses", "end": {"x": samples["pos_x"][n // 4], "z": samples["pos_z"][n // 4]}},
        {"end": {"x": samples["pos_x"][n // 2], "z": samples["pos_z"][n // 2]}},
    ]})
    assert resp.status_code == 200
    assert [s["n"] for s in resp.json()["sectors"]] == [1, 2]

    body = (await c.get(f"/api/analysis/sectors?session_id={laps[0]['session_id']}")).json()
    assert body["source"] == "track"
    assert [s["name"] for s in body["sectors"]] == ["Esses", "", ""]
    assert all(len(lap["splits_ms"]) == 3 for lap in body["laps"])

    ref = laps[0]["id"]
    compare = (await c.get(f"/api/analysis/compare?laps={ref}&ref={ref}")).json()
    windows = compare["laps"][str(ref)]["sectors"]
    assert [w["name"] for w in windows] == ["Esses", "", ""]
    assert all(w["authored"] is True for w in windows)


async def test_an_unknown_session_has_no_sectors(client) -> None:  # noqa: F811
    c, _service, _tmp = client
    assert (await c.get("/api/analysis/sectors?session_id=999")).status_code == 404


# --- in the bundle ---------------------------------------------------------------


async def test_sectors_round_trip_through_the_bundle(client) -> None:  # noqa: F811
    c, service, tmp = client
    _bundle_with_corners(tmp)
    assert service.authored_sectors("Ring") == []  # caches the empty answer
    resp = await c.put("/api/track-bundles/ring/corners",
                       json={"sectors": [{"n": 7, "name": "S1", "end": {"x": 5, "z": 5}}]})
    assert resp.status_code == 200
    assert resp.json()["sectors"] == [{"n": 1, "name": "S1", "end": {"x": 5.0, "z": 5.0}}]
    # The save drops the cached empty set, like it does for corners.
    assert [s["name"] for s in service.authored_sectors("Ring")] == ["S1"]

    got = (await c.get("/api/track-bundles/ring/corners")).json()
    assert got["sectors"][0]["name"] == "S1"
    exported = (await c.get("/api/track-bundles/ring")).json()
    assert exported["sectors"][0]["name"] == "S1"
    assert (await c.get("/api/track-bundles")).json()[0]["sectors"] == 1

    # A survey run never touches them.
    source = track_bundle.source_id(tmp)
    track_bundle.save(
        tmp, "Ring",
        [track_bundle.new_edge(99.0, 5.0, 1.0, 0.0, "L", "wall", 2, source, 1.6)],
        [], count_run=True,
    )
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None and [s["name"] for s in doc["sectors"]] == ["S1"]


async def test_import_never_overwrites_authored_sectors(client) -> None:  # noqa: F811
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    await c.put("/api/track-bundles/ring/corners",
                json={"sectors": [{"name": "Mine", "end": {"x": 5, "z": 5}}]})
    incoming = _foreign_bundle(track="Ring")
    incoming["sectors"] = [{"n": 1, "name": "Theirs", "end": {"x": 5, "z": 5}}]
    assert (await c.post("/api/track-bundles/import", json=incoming)).status_code == 200
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None and [s["name"] for s in doc["sectors"]] == ["Mine"]

    # ...but an import can give a circuit its first ones.
    await c.put("/api/track-bundles/ring/corners", json={"sectors": []})
    assert (await c.post("/api/track-bundles/import", json=incoming)).status_code == 200
    doc = track_bundle.load(tmp, "Ring")
    assert doc is not None and [s["name"] for s in doc["sectors"]] == ["Theirs"]


async def test_sectors_are_rejected_when_they_are_not_sectors(client) -> None:  # noqa: F811
    c, _service, tmp = client
    _bundle_with_corners(tmp)
    for bad in (
        {"sectors": [{"end": None}]},
        {"sectors": [{"end": {"x": "there", "z": 5}}]},
        {"sectors": [{"name": "x" * 500, "end": {"x": 1, "z": 2}}]},
        {"sectors": [{"end": {"x": 1, "z": 2}}] * (track_bundle.MAX_SECTORS + 1)},
    ):
        assert (await c.put("/api/track-bundles/ring/corners", json=bad)).status_code == 400


def test_a_bundle_written_before_sectors_existed_reads_as_having_none(tmp_path) -> None:
    doc = _foreign_bundle(track="Old", version=5)
    doc["meta"]["source_runs"] = {"beef0000cafe": 4}
    track_bundle.merge_document(tmp_path, track_bundle.validate_document(doc))
    stored = track_bundle.load(tmp_path, "Old")
    assert stored is not None and stored["sectors"] == []
