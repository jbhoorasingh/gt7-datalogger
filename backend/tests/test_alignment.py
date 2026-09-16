"""Lining laps up by where they were on track (app.processing.alignment).

The geometry here is exact, so "the same place" is known: two laps on
concentric circles are level when they are at the same angle, whatever
distance each has covered.
"""

import math

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.processing import alignment, analysis
from app.processing.cars import CarDatabase
from app.service import TelemetryService
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.circle_track import RADIUS, TICK, Driver

OMEGA = 50.0 / RADIUS
FULL = 2 * math.pi


def circle_lap(
    radius: float = RADIUS,
    omega: float = OMEGA,
    start: float = 0.0,
    turns: float = 1.0,
    jumps: dict[int, float] | None = None,
) -> dict[str, list[float]]:
    """Samples of a lap round a circle; `jumps` moves the car by an angle at
    a tick (GT7 resetting it) while its distance carries on regardless."""
    n = int(turns * FULL / omega / TICK)
    out: dict[str, list[float]] = {k: [] for k in ("t", "dist", "speed", "pos_x", "pos_z")}
    theta = start
    for k in range(n):
        theta += (jumps or {}).get(k, 0.0)
        out["t"].append(k * TICK)
        out["dist"].append(radius * omega * k * TICK)
        out["speed"].append(radius * omega * 3.6)
        out["pos_x"].append(radius * math.cos(theta))
        out["pos_z"].append(radius * math.sin(theta))
        theta += omega * TICK
    return out


def angle_of(x: float, z: float) -> float:
    a = math.atan2(z, x)
    return a if a >= -1e-9 else a + FULL


def test_a_wider_line_is_lined_up_by_place_not_distance() -> None:
    ref = circle_lap()
    wide = circle_lap(radius=RADIUS + 10)  # 3 % more distance per lap
    moved = alignment.align_to_reference(wide, alignment.ReferencePath(ref))
    assert moved is not None
    # Own distance ends 63 m past the reference's; aligned, the laps agree.
    assert wide["dist"][-1] - ref["dist"][-1] == pytest.approx(62.8, abs=0.5)
    assert moved["dist"][-1] == pytest.approx(ref["dist"][-1], abs=0.5)
    for k in range(0, len(ref["t"]), 97):
        at = angle_of(wide["pos_x"][k], wide["pos_z"][k])
        assert moved["dist"][k] == pytest.approx(RADIUS * at, abs=0.3)
    # Everything else is the lap's own.
    assert moved["t"] is wide["t"] and moved["pos_x"] is wide["pos_x"]


def test_the_delta_compares_the_same_place() -> None:
    """Level laps on different lines: the old axis invented a gap growing to
    over a second; the aligned one finds none."""
    ref = circle_lap()
    wide = circle_lap(radius=RADIUS + 10)
    path = alignment.ReferencePath(ref)
    old = analysis.time_delta_series(wide, ref, 5.0)["delta_ms"]
    new = analysis.time_delta_series(alignment.align_to_reference(wide, path), ref, 5.0)
    assert max(abs(d) for d in old) > 1000
    assert max(abs(d) for d in new["delta_ms"]) < 10


def figure_eight(scale: float, speed: float, phase: float = 0.0) -> dict[str, list[float]]:
    """A lap of a lemniscate — a circuit that crosses itself, like Suzuka.
    `phase` shifts the speed along the lap so two laps differ in timing."""
    pts = [
        (scale * math.cos(u), scale * math.sin(u) * math.cos(u))
        for u in (FULL * i / 20000 for i in range(20001))
    ]
    arc = [0.0]
    for (x0, z0), (x1, z1) in zip(pts, pts[1:], strict=False):
        arc.append(arc[-1] + math.hypot(x1 - x0, z1 - z0))
    out: dict[str, list[float]] = {k: [] for k in ("t", "dist", "speed", "pos_x", "pos_z")}
    s, t, j = 0.0, 0.0, 0
    while s < arc[-1]:
        while arc[j + 1] < s:
            j += 1
        f = (s - arc[j]) / (arc[j + 1] - arc[j])
        out["t"].append(t)
        out["dist"].append(s)
        v = speed * (1 + 0.3 * math.sin(FULL * s / arc[-1] + phase))
        out["speed"].append(v * 3.6)
        out["pos_x"].append(pts[j][0] + f * (pts[j + 1][0] - pts[j][0]))
        out["pos_z"].append(pts[j][1] + f * (pts[j + 1][1] - pts[j][1]))
        s += v * TICK
        t += TICK
    return out


def test_a_circuit_that_crosses_itself_never_switches_branch() -> None:
    ref = figure_eight(400.0, 45.0)
    other = figure_eight(400.0, 45.0, phase=2.0)
    moved = alignment.align_to_reference(other, alignment.ReferencePath(ref))
    assert moved is not None
    # Same path, so the aligned distance is the lap's own — through both
    # passes over the crossing.
    worst = max(abs(a - b) for a, b in zip(moved["dist"], other["dist"], strict=True))
    assert worst < 0.5


def test_a_reset_forward_is_followed_and_a_rewind_is_held() -> None:
    ref = circle_lap()
    path = alignment.ReferencePath(ref)
    k = 900  # ~15 s in, ~750 m round
    # GT7 puts the car 300 m further on: the lap picks it up there.
    ahead = alignment.align_to_reference(circle_lap(jumps={k: 300 / RADIUS}), path)
    assert ahead is not None
    assert ahead["dist"][k] - ahead["dist"][k - 1] == pytest.approx(300 + 50 * TICK, abs=1)
    # A rewind 100 m back: the axis holds until the car is past where it was.
    back = alignment.align_to_reference(circle_lap(jumps={k: -100 / RADIUS}, turns=1.1), path)
    assert back is not None
    held = back["dist"][k - 1]
    assert all(v == held for v in back["dist"][k : k + 100])
    assert all(b >= a for a, b in zip(back["dist"], back["dist"][1:], strict=False))


def test_laps_that_cannot_be_placed_keep_their_own_distance() -> None:
    path = alignment.ReferencePath(circle_lap())
    # Somewhere else entirely.
    far = circle_lap()
    far["pos_x"] = [x + 5000 for x in far["pos_x"]]
    assert alignment.align_to_reference(far, path) is None
    # Mostly off the reference's path (a different layout): lost too often.
    other_layout = circle_lap(radius=RADIUS + 80)
    assert alignment.align_to_reference(other_layout, path) is None


def test_a_lap_from_a_grid_ahead_starts_where_it_started() -> None:
    path = alignment.ReferencePath(circle_lap())
    grid = alignment.align_to_reference(
        circle_lap(start=120 / RADIUS, turns=1 - 120 / (RADIUS * FULL)), path
    )
    assert grid is not None
    assert grid["dist"][0] == pytest.approx(120, abs=1)
    # A grid a little behind the line is placed before the path's start, on
    # the first segment extended backwards...
    behind = alignment.align_to_reference(circle_lap(start=-60 / RADIUS, turns=0.05), path)
    assert behind is not None
    assert behind["dist"][0] == pytest.approx(-60, abs=1)
    # ...but one far back round a curve cannot be: found only near the END of
    # the path, it would need an axis running from minus a lap.
    far_back = circle_lap(start=-300 / RADIUS, turns=0.02)
    assert alignment.align_to_reference(far_back, path) is None


def test_events_move_with_the_lap() -> None:
    events = [{"type": "lockup", "start_dist": 100.0, "end_dist": 110.0, "severity": 0.5}]
    own = [0.0, 100.0, 200.0]
    aligned = [0.0, 97.0, 194.0]
    moved = alignment.remap_events(events, own, aligned)
    assert moved == [{"type": "lockup", "start_dist": 97.0, "end_dist": 106.7, "severity": 0.5}]
    assert events[0]["start_dist"] == 100.0  # not mutated


# --- through the API and the live stream ------------------------------------


@pytest.fixture
async def client(tmp_path):
    settings = Settings(source="udp", db_path=tmp_path / "test.db", ws_rate=1000)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    service.processor.min_lap_ticks = 1
    app = create_app()
    app.router.lifespan_context = None  # type: ignore[assignment]
    app.state.service = service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, service
    await engine.dispose()


LAP_MS = round(FULL / OMEGA * 1000)


async def drive_laps(service: TelemetryService, radii: list[float]) -> None:
    driver = Driver()
    for n, radius in enumerate(radii, start=1):
        for p in driver.lap(n, radius=radius, last_lap_ms=LAP_MS if n > 1 else -1):
            await service._on_packet(p)
    await service._on_packet(driver.cross(len(radii) + 1, LAP_MS, radius=radii[-1]))


async def test_compare_lines_laps_up_and_carries_a_clock_track(client) -> None:
    c, service = client
    await drive_laps(service, [RADIUS, RADIUS + 10])
    laps = sorted((await c.get("/api/laps")).json(), key=lambda lap: lap["number"])
    ref, wide = laps[0]["id"], laps[1]["id"]
    data = (await c.get(f"/api/analysis/compare?laps={wide}&ref={ref}")).json()

    entry = data["laps"][str(wide)]
    assert entry["aligned"] is True
    assert data["laps"][str(ref)]["aligned"] is True
    # On the reference's axis, level all lap: no invented gap.
    assert max(abs(d) for d in entry["delta"]["delta_ms"]) < 25
    ref_end = data["laps"][str(ref)]["series"]["dist"][-1]
    assert entry["series"]["dist"][-1] == pytest.approx(ref_end, abs=1)
    # The clock track: every 50 ms of the lap's own time, to its end.
    track = entry["track"]
    assert track["t"][1] - track["t"][0] == pytest.approx(0.05)
    assert track["t"][-1] == pytest.approx(entry["series"]["t"][-1], abs=0.05)
    assert len(track["pos_x"]) == len(track["t"])


async def test_the_live_delta_compares_the_same_place(client) -> None:
    """Driving level with the best lap on a tighter line: the old delta read
    a growing gap from the distance the two lines differ by."""
    c, service = client
    await drive_laps(service, [RADIUS + 10])  # the best so far, wide
    assert service._best_ref is not None
    driver = Driver()
    driver.pid = service.processor._last_pid
    deltas = []
    for k, p in enumerate(driver.lap(2, radius=RADIUS, last_lap_ms=LAP_MS)):
        if k == 0:
            continue  # lap 2's first packet was already fed as lap 1's end
        await service._on_packet(p)
        if k % 300 == 0:
            deltas.append(service._live_frame(p)["delta_ms"])
    assert deltas and all(d is not None and abs(d) < 25 for d in deltas)


async def test_deviation_uses_counting_laps_lined_up(client) -> None:
    c, service = client
    driver = Driver()
    grid = 40 / RADIUS
    # Lap 1 from a grid ahead of the line: partial, and quicker than any lap.
    for p in driver.lap(1, start=grid):
        await service._on_packet(p)
    for n, radius in ((2, RADIUS), (3, RADIUS + 10)):
        for p in driver.lap(n, radius=radius, last_lap_ms=LAP_MS - 2000 if n == 2 else LAP_MS):
            await service._on_packet(p)
    await service._on_packet(driver.cross(4, LAP_MS + 1, radius=RADIUS + 10))
    laps = {lap["number"]: lap for lap in (await c.get("/api/laps")).json()}
    assert laps[1]["counts_for_best"] is False

    data = (await c.get(f"/api/analysis/deviation?session_id={service.session_id}")).json()
    assert sorted(data["lap_ids"]) == sorted([laps[2]["id"], laps[3]["id"]])
    # Level at every place, on lines 10 m apart: 180 and 186 km/h all lap,
    # a spread of exactly 3 km/h and nothing else.
    assert min(data["deviation"]) == pytest.approx(3.0, abs=0.05)
    assert max(data["deviation"]) == pytest.approx(3.0, abs=0.05)


async def test_stored_laps_are_rechecked_once(tmp_path) -> None:
    import logging

    from app.main import LAP_START_CHECK_KEY, LAP_START_CHECK_VERSION, recheck_lap_starts

    engine = make_engine(tmp_path / "once.db")
    await init_db(engine)
    repo = Repository(make_session_factory(engine))
    log = logging.getLogger("test")
    await recheck_lap_starts(repo, {}, log)
    assert (await repo.get_settings())[LAP_START_CHECK_KEY] == LAP_START_CHECK_VERSION

    calls = []
    repo.recheck_lap_starts = lambda: calls.append(1)  # type: ignore[method-assign]
    await recheck_lap_starts(repo, {LAP_START_CHECK_KEY: LAP_START_CHECK_VERSION}, log)
    assert calls == []
    await engine.dispose()
