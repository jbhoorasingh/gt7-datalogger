"""Where a lap begins: the grid-start guard and the half-gap clock anchor.

Lap 1 of a race is timed from the grid, not the line — 120 m ahead of it at
Red Bull Ring — yet covers well over 97 % of the lap, so the span guard alone
let it win bests. And a lap's first sample lands somewhere inside the gap
since the previous one, so its time and distance must start from the same
guess at where the line was.
"""

import math

import pytest

from app.processing.cars import Car
from app.processing.laps import (
    CompletedLap,
    LapProcessor,
    SessionInfo,
    anchor_at_line,
    decode_samples,
)
from app.storage.db import init_db, make_engine, make_session_factory
from app.storage.repository import Repository
from tests.circle_track import RADIUS, TICK, Driver

OMEGA = 50.0 / RADIUS  # 50 m/s on the default radius
LAP_S = 2 * math.pi / OMEGA  # ~37.7 s
FULL = 2 * math.pi


class Collector:
    def __init__(self) -> None:
        self.laps: list[CompletedLap] = []

    async def on_lap(self, lap: CompletedLap) -> None:
        self.laps.append(lap)

    async def on_session(self, info: SessionInfo) -> None:
        pass


@pytest.fixture
def setup() -> tuple[LapProcessor, Collector, Driver]:
    c = Collector()
    return LapProcessor(on_lap=c.on_lap, on_session=c.on_session, min_lap_ticks=1), c, Driver()


async def drive(proc: LapProcessor, packets) -> None:
    for p in packets:
        await proc.feed(p)


def ms(seconds: float) -> int:
    return round(seconds * 1000)


async def race(proc, driver, lap1_start: float, lap1_ms: int, laps: int = 3, **lap1) -> None:
    """Lap 1 from `lap1_start` (radians; the grid), then full laps."""
    await drive(proc, driver.lap(1, start=lap1_start, end=FULL, **lap1))
    await drive(proc, driver.lap(2, last_lap_ms=lap1_ms))
    for n in range(3, laps + 1):
        await drive(proc, driver.lap(n, last_lap_ms=ms(LAP_S) + n))
    await proc.feed(driver.cross(laps + 1, ms(LAP_S) + laps + 1))


async def test_a_grid_ahead_of_the_line_is_not_a_lap_time(setup) -> None:
    proc, c, driver = setup
    grid = 40 / RADIUS  # 40 m past the line: 98 % of the lap, past the span guard
    lap1_ms = ms((FULL - grid) / OMEGA)
    await race(proc, driver, grid, lap1_ms)

    assert [lap.number for lap in c.laps] == [1, 2, 3]
    lap1 = c.laps[0]
    # Partial the moment it completes — never the session best, not even
    # for the lap before lap 2 finishes.
    assert lap1.counts_for_best is False
    assert lap1.excluded_lap_numbers == [1]
    assert lap1.partial_lap_numbers == [1]
    assert lap1.session_best_before_ms == -1
    assert all(lap.counts_for_best for lap in c.laps[1:])
    assert proc.session.best_lap_time_ms == ms(LAP_S) + 3


async def test_a_grid_behind_the_line_is_not_a_lap_time_either(setup) -> None:
    proc, c, driver = setup
    await race(proc, driver, -60 / RADIUS, ms((FULL + 60 / RADIUS) / OMEGA))
    assert [lap.counts_for_best for lap in c.laps] == [False, True, True]


async def test_a_lap_1_from_the_line_counts(setup) -> None:
    """Time trials: the out-lap ends at the line, so lap 1 starts there."""
    proc, c, driver = setup
    await race(proc, driver, 0.0, ms(LAP_S) - 500)
    assert [lap.counts_for_best for lap in c.laps] == [True, True, True]
    assert proc.session.best_lap_time_ms == ms(LAP_S) - 500


async def test_crossing_the_line_wide_is_still_crossing_it(setup) -> None:
    """Laps cross the line at different points across the track — up to 6 m
    apart on real recordings. Only a start off the circuit altogether (a grid
    on a parallel stretch) is not at the line."""
    proc, c, driver = setup
    await race(proc, driver, 0.0, ms(LAP_S), radius=RADIUS + 8)
    assert c.laps[0].counts_for_best is True

    off_circuit = Collector()
    proc = LapProcessor(
        on_lap=off_circuit.on_lap, on_session=off_circuit.on_session, min_lap_ticks=1
    )
    await race(proc, Driver(), 0.0, ms(LAP_S), radius=RADIUS + 40)
    assert off_circuit.laps[0].counts_for_best is False


async def test_dropped_frames_at_the_line_do_not_look_like_a_grid(setup) -> None:
    """A first sample several frames past the line is still at the line."""
    proc, c, driver = setup
    await drive(proc, driver.lap(1))
    await drive(proc, driver.lap(2, last_lap_ms=ms(LAP_S), first_gap=9))
    await drive(proc, driver.lap(3, last_lap_ms=ms(LAP_S) + 1, first_gap=1))
    await proc.feed(driver.cross(4, ms(LAP_S) + 2))
    assert [lap.counts_for_best for lap in c.laps] == [True, True, True]


async def test_a_redriven_lap_number_is_judged_afresh(setup) -> None:
    proc, c, driver = setup
    grid = 40 / RADIUS
    await race(proc, driver, grid, ms((FULL - grid) / OMEGA), laps=2)
    assert proc.excluded_lap_numbers() == {1}
    # GT7 re-reports lap 1 after a rewind, and this lap 1 began at the line
    # (lap 2's own start marks it): the grid verdict was about the lap it
    # replaced.
    lap2 = c.laps[1].samples
    redriven = CompletedLap(
        number=1, time_ms=ms(LAP_S), finished_at="", car_id=7,
        samples=lap2, fuel_start=0.0, fuel_end=0.0,
    )
    proc._apply_span_guard(redriven, lap2, (lap2["pos_x"][0], lap2["pos_z"][0], 1.0))
    assert redriven.counts_for_best is True
    assert proc.excluded_lap_numbers() == set()


# --- the clock anchor ----------------------------------------------------------


async def test_first_sample_sits_half_a_gap_in_on_both_axes(setup) -> None:
    proc, c, driver = setup
    await drive(proc, driver.lap(1))
    await drive(proc, driver.lap(2, last_lap_ms=ms(LAP_S), first_gap=6))
    await proc.feed(driver.cross(3, ms(LAP_S)))
    lap2 = c.laps[1].samples
    assert lap2["t"][0] == pytest.approx(3 * TICK, abs=1e-4)
    assert lap2["dist"][0] == pytest.approx(50.0 * 3 * TICK, abs=0.01)
    # Time and distance describe the same instants all lap long.
    for k in (0, 100, len(lap2["t"]) - 1):
        assert lap2["dist"][k] == pytest.approx(50.0 * lap2["t"][k], abs=0.02)


def test_laps_recorded_before_the_anchor_are_converted_on_read() -> None:
    # The old recorder: clock from 0, distance a whole 4-frame gap in.
    legacy = {
        "t": [0.0, 0.0167, 0.0333],
        "dist": [4.0, 5.0, 6.0],
        "speed": [216.0, 216.0, 216.0],  # 60 m/s: 4 m is 4 frames
    }
    out = anchor_at_line(legacy)
    gap_s = 4 / 60
    assert out["t"][0] == pytest.approx(gap_s / 2, abs=1e-4)
    assert out["dist"][0] == pytest.approx(2.0)
    assert out["dist"][0] == pytest.approx(60.0 * out["t"][0], abs=0.01)
    assert out["t"][1] - out["t"][0] == pytest.approx(0.0167)  # steps unchanged
    # Converted once: it now reads as current, so a second pass is a no-op.
    again = anchor_at_line({k: list(v) for k, v in out.items()})
    assert again == out
    # A lap cut out of a buffer (salvage) starts both axes at 0: left alone.
    salvaged = {"t": [0.0, 0.0167], "dist": [0.0, 1.0], "speed": [216.0, 216.0]}
    assert anchor_at_line(dict(salvaged)) == salvaged
    # A stationary first sample still converts to something recognisable.
    crawl = anchor_at_line({"t": [0.0, 1.0], "dist": [0.01, 0.02], "speed": [0.0, 0.1]})
    assert crawl["t"][0] > 0
    assert decode_samples('{"t": [0.0], "dist": [4.0], "speed": [216.0]}')["t"][0] > 0


# --- stored laps ---------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    await init_db(engine)
    yield Repository(make_session_factory(engine))
    await engine.dispose()


async def recorded_laps(start_lap1: float) -> list[CompletedLap]:
    c = Collector()
    proc = LapProcessor(on_lap=c.on_lap, on_session=c.on_session, min_lap_ticks=1)
    await race(proc, Driver(), start_lap1, ms((FULL - start_lap1) / OMEGA), laps=3)
    for lap in c.laps:
        lap.counts_for_best = True  # as a recorder without the check stored them
    return c.laps


async def store(repo: Repository, laps: list[CompletedLap], numbers=None) -> dict[int, int]:
    session = await repo.create_session(
        SessionInfo(car_id=7, started_at="2026-09-16T00:00:00Z"), Car(id=7, name="Car 7")
    )
    ids = {}
    for lap in laps:
        if numbers is None or lap.number in numbers:
            ids[lap.number] = await repo.save_lap(session, lap)
    return ids


async def full_laps(repo: Repository, ids: dict[int, int]) -> dict[int, bool]:
    return {n: (await repo.get_lap(i, with_samples=False))["full_lap"] for n, i in ids.items()}


async def test_stored_grid_starts_are_found(repo) -> None:
    grid_race = await store(repo, await recorded_laps(40 / RADIUS))
    time_trial = await store(repo, await recorded_laps(0.0))
    # The user already ruled on lap 3 of the time trial; nothing touches that.
    await repo.set_lap_best_override(time_trial[3], False, "dirty")

    assert await repo.recheck_lap_starts() == 1
    assert await full_laps(repo, grid_race) == {1: False, 2: True, 3: True}
    assert await full_laps(repo, time_trial) == {1: True, 2: True, 3: True}
    lap3 = await repo.get_lap(time_trial[3], with_samples=False)
    assert (lap3["best_override"], lap3["exclude_reason"]) == (False, "dirty")
    # Idempotent: a second pass finds nothing new.
    assert await repo.recheck_lap_starts() == 0


async def test_a_last_lap_is_judged_against_a_lap_that_began_at_the_line(repo) -> None:
    """Session 249's case: recording began during lap 2, so lap 2's start
    marks nothing — the last lap must be judged against lap 3, whose
    predecessor was stored."""
    laps = await recorded_laps(0.0)
    mid_lap = laps[0]
    # Lap "2" recorded from wherever the car was when capture began.
    mid_lap.number = 2
    half = len(mid_lap.samples["t"]) // 2
    mid_lap.samples = {k: v[half:] for k, v in mid_lap.samples.items()}
    laps[1].number, laps[2].number = 3, 4
    ids = await store(repo, [mid_lap, laps[1], laps[2]])

    await repo.recheck_lap_starts()
    flags = await full_laps(repo, ids)
    assert flags[4] is True  # judged against lap 3, not the mid-lap start
    assert flags[2] is False  # and lap 2 itself did not start at the line
