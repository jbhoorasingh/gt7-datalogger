"""Tests for native sector voice callouts."""

import pytest

from app.race_engineer.formatter import spoken_sector_decimal
from app.race_engineer.sectors import (
    LiveSectorTracker,
    interpolated_boundary_crossing,
    reference_sector_directions,
    validated_sector_boundaries,
)


@pytest.mark.parametrize(
    ("value", "places", "rounding", "expected"),
    [
        (0.369, 3, "round", "point three six nine"),
        (0.300, 3, "round", "point three zero zero"),
        (1.234, 3, "round", "one point two three four"),
        (0.369, 2, "round", "point three seven"),
        (0.369, 2, "truncate", "point three six"),
        (42.183, 3, "round", "forty-two point one eight three"),
        (42.0, 3, "round", "forty-two point zero zero zero"),
        (0.0, 3, "round", "zero point zero zero zero"),
    ],
)
def test_spoken_sector_decimal(
    value: float,
    places: int,
    rounding: str,
    expected: str,
) -> None:
    assert spoken_sector_decimal(value, places, rounding) == expected


@pytest.mark.parametrize(
    ("value", "places", "rounding", "expected"),
    [
        (59.9996, 3, "round", "sixty point zero zero zero"),
        (59.9996, 3, "truncate", "fifty-nine point nine nine nine"),
        (0.0004, 3, "round", "zero point zero zero zero"),
        (0.0009, 3, "truncate", "zero point zero zero zero"),
        (1.235, 2, "round", "one point two four"),
        (1.235, 2, "truncate", "one point two three"),
        (0.5, 1, "round", "point five"),
    ],
)
def test_spoken_sector_decimal_edge_cases(
    value: float,
    places: int,
    rounding: str,
    expected: str,
) -> None:
    assert spoken_sector_decimal(value, places, rounding) == expected


@pytest.mark.parametrize("places", [0, 4, -1])
def test_spoken_sector_decimal_rejects_invalid_precision(places: int) -> None:
    with pytest.raises(ValueError, match="precision"):
        spoken_sector_decimal(1.234, places)


def test_spoken_sector_decimal_rejects_invalid_rounding() -> None:
    with pytest.raises(ValueError, match="rounding"):
        spoken_sector_decimal(1.234, 3, "invalid")



def test_sector_voice_accepts_complete_authored_boundaries() -> None:
    authored = [
        {"n": 1, "end": {"x": 100.0, "z": 200.0}},
        {"n": 2, "end": {"x": 300.0, "z": 400.0}},
    ]

    points = validated_sector_boundaries(authored)

    assert points is not None
    assert len(points) == 2
    assert points[0]["x"] == 100.0
    assert points[1]["z"] == 400.0


@pytest.mark.parametrize(
    "authored",
    [
        [],
        [{"n": 2, "end": {"x": 100.0, "z": 200.0}}],
        [
            {"n": 1, "end": {"x": 100.0, "z": 200.0}},
            {"n": 2, "end": {"x": None, "z": 400.0}},
        ],
        [
            {"n": 1, "end": {"x": 100.0, "z": 200.0}},
            {"n": 1, "end": {"x": 300.0, "z": 400.0}},
        ],
    ],
)
def test_sector_voice_rejects_incomplete_boundaries(authored: list) -> None:
    assert validated_sector_boundaries(authored) is None


def test_sector_voice_interpolates_live_crossing() -> None:
    previous = (95.0, 200.0, 30.0)
    current = (105.0, 200.0, 30.1)
    boundary = {"n": 1, "name": "", "x": 100.0, "z": 200.0}

    crossing = interpolated_boundary_crossing(
        previous,
        current,
        boundary,
    )

    assert crossing is not None
    assert crossing == pytest.approx(30.05)


def test_sector_voice_rejects_distant_crossing() -> None:
    previous = (95.0, 250.0, 30.0)
    current = (105.0, 250.0, 30.1)
    boundary = {"n": 1, "name": "", "x": 100.0, "z": 200.0}

    assert interpolated_boundary_crossing(
        previous,
        current,
        boundary,
    ) is None


def test_sector_tracker_announces_boundaries_in_order() -> None:
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    # S2 cannot trigger before S1.
    assert tracker.observe(1, (195.0, 200.0, 20.0)) is None
    assert tracker.observe(1, (205.0, 200.0, 20.1)) is None

    # Start a fresh lap, then cross S1.
    assert tracker.observe(2, (95.0, 200.0, 30.0)) is None
    first = tracker.observe(2, (105.0, 200.0, 30.1))

    assert first is not None
    assert first.sector == 1
    assert first.time_ms == pytest.approx(30050.0)

    # Repeated proximity must not announce S1 again.
    assert tracker.observe(2, (95.0, 200.0, 30.2)) is None
    assert tracker.observe(2, (105.0, 200.0, 30.3)) is None

    # Crossing S2 produces the individual S2 time.
    assert tracker.observe(2, (195.0, 200.0, 50.0)) is None
    second = tracker.observe(2, (205.0, 200.0, 50.1))

    assert second is not None
    assert second.sector == 2
    assert second.time_ms == pytest.approx(20000.0)
    assert second.cumulative_ms == pytest.approx(50050.0)


def test_sector_tracker_resets_on_new_lap() -> None:
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    assert tracker.observe(1, (95.0, 200.0, 10.0)) is None
    assert tracker.observe(1, (105.0, 200.0, 10.1)) is not None

    # The same boundary should work again on lap 2.
    assert tracker.observe(2, (95.0, 200.0, 11.0)) is None
    first = tracker.observe(2, (105.0, 200.0, 11.1))

    assert first is not None
    assert first.sector == 1
    assert first.time_ms == pytest.approx(11050.0)


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        # Telemetry interruption longer than one second.
        ((95.0, 200.0, 30.0), (105.0, 200.0, 32.0)),

        # Time moving backwards.
        ((95.0, 200.0, 30.1), (105.0, 200.0, 30.0)),

        # Identical timestamps.
        ((95.0, 200.0, 30.0), (105.0, 200.0, 30.0)),

        # Unrealistically large movement between samples.
        ((50.0, 200.0, 30.0), (150.0, 200.0, 30.1)),

        # Stationary car.
        ((100.0, 200.0, 30.0), (100.0, 200.0, 30.1)),

        # Invalid position.
        ((float("nan"), 200.0, 30.0), (105.0, 200.0, 30.1)),

        # Invalid timestamp.
        ((95.0, 200.0, float("inf")), (105.0, 200.0, 30.1)),
    ],
)
def test_sector_voice_rejects_invalid_live_samples(
    previous: tuple[float, float, float],
    current: tuple[float, float, float],
) -> None:
    boundary = {"n": 1, "name": "", "x": 100.0, "z": 200.0}

    assert interpolated_boundary_crossing(
        previous,
        current,
        boundary,
    ) is None


def test_sector_voice_does_not_trigger_when_turning_away() -> None:
    """Approaching a boundary and retreating is not a crossing."""
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    samples = [
        (95.0, 200.0, 10.0),
        (98.0, 200.0, 10.1),
        (99.5, 200.0, 10.2),
        (98.0, 200.0, 10.3),
        (95.0, 200.0, 10.4),
    ]

    results = [tracker.observe(1, sample) for sample in samples]

    assert all(result is None for result in results)


def test_sector_voice_rejects_reverse_crossing() -> None:
    """Crossing a sector boundary backwards must not announce it."""
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    # The car approaches S1 from the wrong direction.
    assert tracker.observe(1, (105.0, 200.0, 30.0)) is None

    # It then crosses S1 backwards.
    crossing = tracker.observe(1, (95.0, 200.0, 30.1))

    assert crossing is None


def test_sector_voice_derives_reference_directions() -> None:
    boundaries = [
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ]

    samples = {
        "dist": [float(i * 5) for i in range(61)],
        "pos_x": [float(i * 5) for i in range(61)],
        "pos_z": [200.0] * 61,
        "speed": [100.0] * 61,
    }

    directions = reference_sector_directions(samples, boundaries)

    assert directions is not None
    assert len(directions) == 2

    # Both boundaries are crossed towards increasing X.
    assert directions[0][0] == pytest.approx(1.0)
    assert directions[0][1] == pytest.approx(0.0)
    assert directions[1][0] == pytest.approx(1.0)
    assert directions[1][1] == pytest.approx(0.0)


def test_sector_voice_rejects_unrelated_reference() -> None:
    boundaries = [
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ]

    samples = {
        "dist": [float(i * 5) for i in range(61)],
        "pos_x": [float(i * 5) for i in range(61)],
        "pos_z": [500.0] * 61,
        "speed": [100.0] * 61,
    }

    assert reference_sector_directions(samples, boundaries) is None



@pytest.mark.parametrize(
    "directions",
    [
        None,
        [],
        [(1.0, 0.0)],
        [(0.0, 0.0), (1.0, 0.0)],
        [(float("nan"), 0.0), (1.0, 0.0)],
    ],
)
def test_sector_voice_requires_valid_reference_directions(
    directions: list[tuple[float, float]] | None,
) -> None:
    """No sector announcement without two valid reference directions."""
    tracker = LiveSectorTracker()

    tracker.configure(
        [
            {"n": 1, "name": "", "x": 100.0, "z": 200.0},
            {"n": 2, "name": "", "x": 200.0, "z": 200.0},
        ],
        directions=directions,
    )

    assert tracker.observe(1, (95.0, 200.0, 30.0)) is None
    assert tracker.observe(1, (105.0, 200.0, 30.1)) is None


def test_sector_voice_resets_when_reference_changes() -> None:
    """Changing reference discards previous sector timing progress."""
    tracker = LiveSectorTracker()

    boundaries = [
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ]

    tracker.configure(
        boundaries,
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )

    # Complete S1 using the original reference.
    assert tracker.observe(1, (95.0, 200.0, 30.0)) is None
    first = tracker.observe(1, (105.0, 200.0, 30.1))

    assert first is not None
    assert first.sector == 1

    # Race Engineer switches to another reference lap.
    tracker.configure(
        boundaries,
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )

    # Crossing S2 must not use timing state from the old reference.
    assert tracker.observe(1, (195.0, 200.0, 50.0)) is None
    assert tracker.observe(1, (205.0, 200.0, 50.1)) is None


@pytest.mark.asyncio
async def test_stale_reference_preparation_cannot_reactivate_sector_tracker(
    monkeypatch,
) -> None:
    """An obsolete worker result must not restore an invalidated reference."""
    import asyncio
    import threading

    from app.processing.laps import SessionInfo
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    mgr.ctx.track_name = "Test Circuit"

    samples = {
        "t": [0.0, 1.0, 2.0, 3.0],
        "dist": [0.0, 10.0, 20.0, 30.0],
        "pos_x": [0.0, 10.0, 20.0, 30.0],
        "pos_z": [0.0, 0.0, 0.0, 0.0],
    }

    authored = [
        {"n": 1, "end": {"x": 10.0, "z": 0.0}},
        {"n": 2, "end": {"x": 20.0, "z": 0.0}},
    ]

    worker_started = threading.Event()
    release_worker = threading.Event()

    def delayed_source(track: str) -> list[dict]:
        assert track == "Test Circuit"
        worker_started.set()
        if not release_worker.wait(timeout=5):
            raise TimeoutError("Test worker was not released")
        return authored

    mgr.sector_source = delayed_source

    monkeypatch.setattr(
        "app.race_engineer.manager.reference_sector_directions",
        lambda reference, boundaries: [(1.0, 0.0), (1.0, 0.0)],
    )

    mgr._pending_reference = samples

    preparation = asyncio.create_task(mgr.refresh_reference())

    try:
        started = await asyncio.to_thread(worker_started.wait, 5)
        assert started, "Reference preparation did not start"

        mgr._invalidate_sector_reference()

        release_worker.set()
        await preparation

        assert mgr._sector_tracker._boundaries == []
        assert mgr._sector_tracker._directions == []
    finally:
        release_worker.set()
        await preparation

@pytest.mark.asyncio
async def test_current_reference_preparation_configures_sector_tracker(
    monkeypatch,
) -> None:
    """A current reference may configure both authored sector boundaries."""
    from app.processing.laps import SessionInfo
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    mgr.ctx.track_name = "Test Circuit"

    samples = {
        "t": [i * 0.25 for i in range(13)],
        "dist": [i * 2.5 for i in range(13)],
        "pos_x": [i * 2.5 for i in range(13)],
        "pos_z": [0.0] * 13,
    }

    authored = [
        {"n": 1, "end": {"x": 10.0, "z": 0.0}},
        {"n": 2, "end": {"x": 20.0, "z": 0.0}},
    ]

    mgr.sector_source = lambda track: authored

    monkeypatch.setattr(
        "app.race_engineer.manager.reference_sector_directions",
        lambda reference, boundaries: [(1.0, 0.0), (1.0, 0.0)],
    )

    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap

    lap = CompletedLap(
        number=1,
        time_ms=3000,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples=samples,
        fuel_start=10.0,
        fuel_end=9.0,
        full_lap=True,
    )
    lap.span_confirmed = True

    mgr.on_lap(lap)
    await mgr.refresh_reference()

    assert mgr.ctx.reference is samples
    assert mgr._sector_tracker._boundaries == [
        {"n": 1, "name": "", "x": 10.0, "z": 0.0},
        {"n": 2, "name": "", "x": 20.0, "z": 0.0},
    ]
    assert mgr._sector_tracker._directions == [(1.0, 0.0), (1.0, 0.0)]
    assert mgr._sector_reference_generation > 0
    assert mgr._sector_reference_splits_ms == pytest.approx(
        [1000.0, 1000.0, 1000.0],
        abs=0.5,
    )

@pytest.mark.asyncio
async def test_chase_reference_prepares_sector_times(monkeypatch) -> None:
    """Chase sector times must belong to the selected stored lap."""
    from app.processing.laps import SessionInfo
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    mgr.ctx.track_name = "Test Circuit"

    # A sample every 0.75 s: live timing never interpolates across a gap
    # longer than a second, and the reference is timed by the same rule.
    samples = {
        "t": [i * 0.75 for i in range(121)],
        "dist": [i * 0.25 for i in range(121)],
        "pos_x": [i * 0.25 for i in range(121)],
        "pos_z": [0.0] * 121,
    }

    authored = [
        {"n": 1, "end": {"x": 10.0, "z": 0.0}},
        {"n": 2, "end": {"x": 20.0, "z": 0.0}},
    ]

    mgr.sector_source = lambda track: authored

    monkeypatch.setattr(
        "app.race_engineer.manager.reference_sector_directions",
        lambda reference, boundaries: [(1.0, 0.0), (1.0, 0.0)],
    )

    mgr.select_chase_reference(42, 90_000, samples)
    await mgr.refresh_reference()

    assert mgr.reference_mode == "chase"
    assert mgr.ctx.reference is samples
    assert mgr.reference_time_ms == 90_000
    assert mgr._sector_reference_splits_ms == pytest.approx(
        [30_000.0, 30_000.0, 30_000.0],
        abs=0.5,
    )

    mgr.select_session_best_reference()

    assert mgr._sector_reference_splits_ms is None
    assert mgr._sector_tracker._boundaries == []


@pytest.mark.asyncio
async def test_faster_lap_replaces_chase_sector_reference(monkeypatch) -> None:
    """A faster lap replaces Chase sector times without retaining stale data."""
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap, SessionInfo
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    mgr.ctx.track_name = "Test Circuit"

    authored = [
        {"n": 1, "end": {"x": 10.0, "z": 0.0}},
        {"n": 2, "end": {"x": 20.0, "z": 0.0}},
    ]
    mgr.sector_source = lambda track: authored

    monkeypatch.setattr(
        "app.race_engineer.manager.reference_sector_directions",
        lambda reference, boundaries: [(1.0, 0.0), (1.0, 0.0)],
    )

    def telemetry(duration_s: float) -> dict[str, list[float]]:
        return {
            "t": [i * duration_s / 120 for i in range(121)],
            "dist": [i * 0.25 for i in range(121)],
            "pos_x": [i * 0.25 for i in range(121)],
            "pos_z": [0.0] * 121,
        }

    historical = telemetry(90.0)
    faster = telemetry(88.5)

    mgr.select_chase_reference(42, 90_000, historical)
    await mgr.refresh_reference()

    assert mgr._sector_reference_splits_ms == pytest.approx(
        [30_000.0, 30_000.0, 30_000.0],
        abs=0.5,
    )

    lap = CompletedLap(
        number=1,
        time_ms=88_500,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples=faster,
        fuel_start=10.0,
        fuel_end=9.0,
        full_lap=True,
    )
    lap.span_confirmed = True

    mgr.on_lap(lap)

    assert mgr.reference_mode == "chase"
    assert mgr.reference_time_ms == 88_500
    assert mgr._pending_reference is faster
    assert mgr._sector_reference_splits_ms is None
    assert mgr._sector_tracker._boundaries == []

    await mgr.refresh_reference()

    assert mgr.ctx.reference is faster
    assert mgr._sector_reference_splits_ms == pytest.approx(
        [29_500.0, 29_500.0, 29_500.0],
        abs=0.5,
    )


def test_manager_observes_live_sectors_only_with_valid_reference() -> None:
    """Live crossings require an active, prepared reference."""
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.enabled = True

    boundaries = [
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ]

    mgr._sector_tracker.configure(
        boundaries,
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )

    # A configured tracker alone is insufficient.
    assert mgr.observe_sector(1, (95.0, 200.0, 30.0)) is None
    assert mgr.observe_sector(1, (105.0, 200.0, 30.1)) is None

    # A prepared reference enables live crossings.
    mgr.ctx.reference = {"t": [0.0, 90.0]}
    mgr._sector_reference_splits_ms = [30_000.0] * 3

    assert mgr.observe_sector(2, (95.0, 200.0, 30.0)) is None

    first = mgr.observe_sector(2, (105.0, 200.0, 30.1))

    assert first is not None
    assert first.sector == 1
    assert first.time_ms == pytest.approx(30_050.0)
    assert first.cumulative_ms == pytest.approx(30_050.0)

    # An invalidated reference cannot produce more crossings.
    mgr._invalidate_sector_reference()

    assert mgr.observe_sector(2, (195.0, 200.0, 50.0)) is None
    assert mgr.observe_sector(2, (205.0, 200.0, 50.1)) is None

def test_manager_interrupted_sample_preserves_sector_progress() -> None:
    """An invalid sample breaks position continuity without losing S1."""
    from app.race_engineer.manager import RaceEngineerManager

    mgr = RaceEngineerManager()
    mgr.enabled = True
    mgr.ctx.reference = {"t": [0.0, 90.0]}
    mgr._sector_reference_splits_ms = [30_000.0] * 3

    mgr._sector_tracker.configure(
        [
            {"n": 1, "name": "", "x": 100.0, "z": 200.0},
            {"n": 2, "name": "", "x": 200.0, "z": 200.0},
        ],
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )

    assert mgr.observe_sector(2, (95.0, 200.0, 30.0)) is None
    first = mgr.observe_sector(2, (105.0, 200.0, 30.1))
    assert first is not None
    assert first.sector == 1

    assert mgr.observe_sector(2, (195.0, 200.0, 50.0)) is None

    # A pause or invalid telemetry interrupts position continuity.
    assert mgr.observe_sector(2, None) is None

    # Resuming beyond S2 must not invent a crossing.
    assert mgr.observe_sector(2, (205.0, 200.0, 50.1)) is None

    # A subsequent genuine crossing should still complete S2.
    assert mgr.observe_sector(2, (195.0, 200.0, 50.2)) is None
    second = mgr.observe_sector(2, (205.0, 200.0, 50.3))

    assert second is not None
    assert second.sector == 2
    assert second.time_ms == pytest.approx(20_200.0)
    assert second.cumulative_ms == pytest.approx(50_250.0)


def test_sector_tracker_completes_third_sector() -> None:
    """S3 uses the completed lap time and the actual S2 crossing."""
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    assert tracker.observe(2, (95.0, 200.0, 30.0)) is None
    first = tracker.observe(2, (105.0, 200.0, 30.1))

    assert first is not None
    assert first.sector == 1

    assert tracker.observe(2, (195.0, 200.0, 50.0)) is None
    second = tracker.observe(2, (205.0, 200.0, 50.1))

    assert second is not None
    assert second.sector == 2
    assert second.cumulative_ms == pytest.approx(50050.0)

    third = tracker.complete(2, 80000.0)

    assert third is not None
    assert third.sector == 3
    assert third.time_ms == pytest.approx(29950.0)
    assert third.cumulative_ms == pytest.approx(80000.0)

    # Completing the same lap twice must not repeat S3.
    assert tracker.complete(2, 80000.0) is None


@pytest.mark.parametrize(
    "failure",
    [
        "missing_s1",
        "missing_s2",
        "wrong_lap",
        "lap_zero",
        "zero_time",
        "equal_to_s2",
        "earlier_than_s2",
        "nan",
        "infinity",
    ],
)
def test_sector_tracker_rejects_invalid_third_sector(failure: str) -> None:
    """S3 must not be fabricated from incomplete or invalid timing."""
    tracker = LiveSectorTracker()
    tracker.configure([
        {"n": 1, "name": "", "x": 100.0, "z": 200.0},
        {"n": 2, "name": "", "x": 200.0, "z": 200.0},
    ], directions=[(1.0, 0.0), (1.0, 0.0)])

    tracker.observe(2, (95.0, 200.0, 30.0))

    if failure != "missing_s1":
        assert tracker.observe(
            2, (105.0, 200.0, 30.1)
        ) is not None

    s2 = None
    if failure not in ("missing_s1", "missing_s2"):
        tracker.observe(2, (195.0, 200.0, 50.0))
        s2 = tracker.observe(2, (205.0, 200.0, 50.1))
        assert s2 is not None

    lap = 2
    finish_ms = 80000.0

    if failure == "wrong_lap":
        lap = 3
    elif failure == "lap_zero":
        lap = 0
    elif failure == "zero_time":
        finish_ms = 0.0
    elif failure == "equal_to_s2":
        finish_ms = s2.cumulative_ms
    elif failure == "earlier_than_s2":
        finish_ms = s2.cumulative_ms - 1000.0
    elif failure == "nan":
        finish_ms = float("nan")
    elif failure == "infinity":
        finish_ms = float("inf")

    assert tracker.complete(lap, finish_ms) is None


def test_sector_voice_dedicated_enable_switch() -> None:
    """Sector speech is opt-in, independently of Race Engineer."""
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager(enabled=True)

    assert manager.enabled is True
    assert manager.sector_voice_enabled is False

    manager.configure(sector_voice_enabled=True)
    assert manager.sector_voice_enabled is True
    assert manager.enabled is True

    manager.configure(sector_voice_enabled=False)
    assert manager.sector_voice_enabled is False
    assert manager.enabled is True


def test_sector_voice_emits_first_sector_comparison() -> None:
    """An enabled S1 crossing produces a reference comparison callout."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager(enabled=True)
    manager.configure(sector_voice_enabled=True)

    # A validated reference with a 30.500-second first sector.
    manager._sector_reference_splits_ms = [
        30500.0, 20000.0, 29500.0,
    ]
    manager.ctx.reference = object()

    crossing = SectorCrossing(
        sector=1,
        time_ms=30050.0,
        cumulative_ms=30050.0,
    )

    callout = manager.sector_callout(2, crossing)

    assert callout is not None
    assert callout.event_type == "sector_time"
    assert callout.category == "lap"
    assert callout.priority == 55
    assert callout.ttl_ms == 15_000
    assert "sector one" in callout.text.lower()
    assert "faster" in callout.text.lower()


@pytest.mark.parametrize(
    "failure",
    [
        "disabled",
        "engineer_disabled",
        "no_reference",
        "no_splits",
        "invalid_reference",
        "invalid_sector",
        "invalid_duration",
        "invalid_cumulative",
        "nan_duration",
    ],
)
def test_sector_voice_suppresses_invalid_announcements(failure: str) -> None:
    """Only enabled, valid sector comparisons may reach the voice queue."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager(enabled=True)
    manager.configure(sector_voice_enabled=True)
    manager.ctx.reference = object()
    manager._sector_reference_splits_ms = [
        30500.0, 20000.0, 29500.0,
    ]

    sector = 1
    time_ms = 30050.0
    cumulative_ms = 30050.0

    if failure == "disabled":
        manager.configure(sector_voice_enabled=False)
    elif failure == "engineer_disabled":
        manager.configure(enabled=False)
    elif failure == "no_reference":
        manager.ctx.reference = None
    elif failure == "no_splits":
        manager._sector_reference_splits_ms = None
    elif failure == "invalid_reference":
        manager._sector_reference_splits_ms[0] = float("nan")
    elif failure == "invalid_sector":
        sector = 4
    elif failure == "invalid_duration":
        time_ms = -100.0
    elif failure == "invalid_cumulative":
        cumulative_ms = 100.0
    elif failure == "nan_duration":
        time_ms = float("nan")

    crossing = SectorCrossing(
        sector=sector,
        time_ms=time_ms,
        cumulative_ms=cumulative_ms,
    )

    assert manager.sector_callout(2, crossing) is None
    assert manager.stats["emitted"] == 0


@pytest.mark.parametrize("valid", [True, False], ids=["valid", "invalid"])
def test_s3_voice_respects_completed_lap_validity(valid: bool) -> None:
    """S3 speaks only for a valid lap, using the pre-promotion reference."""
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager(enabled=True)
    manager.configure(sector_voice_enabled=True)

    manager.ctx.reference = {
        "t": [0.0, 80.0],
        "dist": [0.0, 100.0],
    }
    manager.ctx.best_lap_ms = 80000
    manager._sector_reference_splits_ms = [
        30500.0, 20000.0, 29500.0,
    ]

    tracker = manager._sector_tracker
    tracker.configure(
        [
            {"n": 1, "name": "", "x": 10.0, "z": 0.0},
            {"n": 2, "name": "", "x": 20.0, "z": 0.0},
        ],
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )

    # Complete both real, authored sector boundaries on lap 2.
    assert tracker.observe(2, (9.0, 0.0, 30.0)) is None
    s1 = tracker.observe(2, (11.0, 0.0, 30.1))
    assert s1 is not None and s1.sector == 1

    assert tracker.observe(2, (19.0, 0.0, 50.0)) is None
    s2 = tracker.observe(2, (21.0, 0.0, 50.1))
    assert s2 is not None and s2.sector == 2

    lap = CompletedLap(
        number=2,
        time_ms=79600,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples={"t": [], "dist": []},
        fuel_start=10.0,
        fuel_end=9.0,
        full_lap=valid,
    )
    lap.span_confirmed = True

    callouts = manager.on_lap(lap)
    sector_callouts = [
        c for c in callouts if c.event_type == "sector_time"
    ]

    if not valid:
        assert sector_callouts == []
        return

    assert len(sector_callouts) == 1
    s3 = sector_callouts[0]

    assert s3.message_args["sector"] == 3
    assert s3.message_args["time_ms"] == pytest.approx(29550.0)
    assert s3.message_args["delta_ms"] == pytest.approx(50.0)
    assert "sector three" in s3.text.lower()
    assert "slower" in s3.text.lower()

    # Completion cannot produce a second S3 announcement.
    assert tracker.complete(2, 79600.0) is None


# Slower than any browser voice at the default rate (about 14 characters a
# second), so the wait it estimates is on the long side.
SPEECH_MS_PER_CHAR = 90


@pytest.mark.parametrize("decimals", [1, 3])
def test_final_sector_outlives_the_lap_announcement_ahead_of_it(
    decimals: int,
) -> None:
    """S3 is emitted with the lap's own announcement, which the browser
    speaks first (it arrives first, at a higher priority). S3 must still be
    live once everything ahead of it has been said."""
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager(enabled=True)
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=decimals,
        sector_voice_content="both",
    )
    manager.ctx.reference = {"t": [0.0, 80.0], "dist": [0.0, 100.0]}
    manager._sector_reference_splits_ms = [30500.0, 20000.0, 29500.0]
    tracker = manager._sector_tracker
    tracker.configure(
        [
            {"n": 1, "name": "", "x": 10.0, "z": 0.0},
            {"n": 2, "name": "", "x": 20.0, "z": 0.0},
        ],
        directions=[(1.0, 0.0), (1.0, 0.0)],
    )
    tracker.observe(2, (9.0, 0.0, 30.0))
    tracker.observe(2, (11.0, 0.0, 30.1))
    tracker.observe(2, (19.0, 0.0, 50.0))
    tracker.observe(2, (21.0, 0.0, 50.1))

    lap = CompletedLap(
        number=2,
        time_ms=81_234,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples={"t": [], "dist": []},
        fuel_start=10.0,
        fuel_end=9.0,
        full_lap=True,
        # A gap to the best lengthens the announcement S3 waits behind.
        session_best_before_ms=80_000,
    )
    lap.span_confirmed = True

    callouts = manager.on_lap(lap)
    s3 = next(c for c in callouts if c.event_type == "sector_time")
    ahead = [
        c for c in callouts
        if c is not s3 and (c is callouts[0] or c.priority > s3.priority)
    ]

    assert any(c.event_type == "lap_time" for c in ahead)
    wait_ms = sum(len(c.text) for c in ahead) * SPEECH_MS_PER_CHAR
    assert s3.ttl_ms > wait_ms


@pytest.mark.asyncio
@pytest.mark.parametrize("comparison", ["individual", "cumulative"])
async def test_lap_driven_like_the_reference_is_level_at_every_sector(
    comparison: str,
) -> None:
    """The reference's sector times come from the rule live laps are timed
    by, so a lap that repeats the reference sample for sample hears "level"
    at every sector, even with each boundary between two samples."""
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap, SessionInfo
    from app.processing.sectors import splits_ms
    from app.race_engineer.manager import RaceEngineerManager

    # 20 s down a straight at 50 m/s and 60 Hz, rounded as the lap processor
    # records it; the first boundary falls between t = 10.0 and 10.0167.
    n = 1200
    samples = {
        "t": [round(i / 60, 4) for i in range(n)],
        "dist": [round(i * 50 / 60, 2) for i in range(n)],
        "pos_x": [round(i * 50 / 60, 2) for i in range(n)],
        "pos_z": [0.0] * n,
        "speed": [180.0] * n,
    }

    def halfway(i: int) -> float:
        return (samples["pos_x"][i] + samples["pos_x"][i + 1]) / 2

    authored = [
        {"n": 1, "end": {"x": halfway(600), "z": 0.0}},
        {"n": 2, "end": {"x": halfway(900), "z": 0.0}},
    ]

    manager = RaceEngineerManager(enabled=True)
    manager.configure(
        sector_voice_enabled=True, sector_voice_comparison=comparison
    )
    manager.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    manager.ctx.track_name = "Test Circuit"
    manager.sector_source = lambda track: authored

    def lap(number: int) -> CompletedLap:
        record = CompletedLap(
            number=number,
            time_ms=20_000,
            finished_at=datetime.now(UTC).isoformat(),
            car_id=7,
            samples=samples,
            fuel_start=10.0,
            fuel_end=9.0,
            full_lap=True,
        )
        record.span_confirmed = True
        return record

    manager.on_lap(lap(1))
    await manager.refresh_reference()

    splits = manager._sector_reference_splits_ms
    assert splits is not None and len(splits) == 3
    assert sum(splits) == pytest.approx(20_000.0)
    # Read off the nearest sample instead, S1 would be half a frame out.
    boundaries = validated_sector_boundaries(authored)
    assert boundaries is not None
    nearest = splits_ms(samples, boundaries, 20_000)
    assert nearest is not None
    assert abs(nearest[0] - splits[0]) > 5

    callouts = []
    for sample in zip(
        samples["pos_x"], samples["pos_z"], samples["t"], strict=True
    ):
        crossing = manager.observe_sector(2, sample)
        if crossing is not None:
            callout = manager.sector_callout(2, crossing)
            assert callout is not None
            callouts.append(callout)
    callouts += [
        c for c in manager.on_lap(lap(2)) if c.event_type == "sector_time"
    ]

    assert [c.metadata["sector"] for c in callouts] == [1, 2, 3]
    for callout in callouts:
        assert callout.metadata["delta_ms"] == pytest.approx(0.0, abs=1e-6)
        assert "level with the reference" in callout.text


@pytest.mark.parametrize(
    ("decimals", "rounding", "expected"),
    [
        (1, "round", "Sector one, point two seconds slower."),
        (2, "round", "Sector one, point two four seconds slower."),
        (3, "round", "Sector one, point two three nine seconds slower."),
        (2, "truncate", "Sector one, point two three seconds slower."),
    ],
)
def test_sector_voice_configurable_precision(
    decimals: int, rounding: str, expected: str
) -> None:
    """Sector speech honours its own decimal and rounding settings."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager(enabled=True)
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=decimals,
        sector_voice_rounding=rounding,
    )
    manager.ctx.reference = object()
    manager._sector_reference_splits_ms = [
        30000.0, 20000.0, 30000.0,
    ]

    crossing = SectorCrossing(
        sector=1,
        time_ms=30239.0,
        cumulative_ms=30239.0,
    )

    callout = manager.sector_callout(2, crossing)

    assert callout is not None
    assert callout.text == expected

@pytest.mark.parametrize(
    "boundary_count",
    [1, 2, 3, 4],
    ids=["2-sectors", "3-sectors", "4-sectors", "5-sectors"],
)
def test_variable_sector_boundary_counts(boundary_count: int) -> None:
    """Any complete ordered set of authored boundaries is accepted."""
    from app.race_engineer.sectors import validated_sector_boundaries

    authored = [
        {
            "n": number,
            "name": f"Sector {number}",
            "end": {
                "x": float(number * 10),
                "z": 0.0,
            },
        }
        for number in range(1, boundary_count + 1)
    ]

    boundaries = validated_sector_boundaries(authored)

    assert boundaries is not None
    assert len(boundaries) == boundary_count
    assert [boundary["n"] for boundary in boundaries] == list(
        range(1, boundary_count + 1)
    )

@pytest.mark.parametrize(
    "boundary_count",
    [1, 2, 3, 4],
    ids=["2-sectors", "3-sectors", "4-sectors", "5-sectors"],
)
def test_variable_sector_reference_directions(boundary_count: int) -> None:
    """Every authored boundary receives its reference-lap travel direction."""
    from app.race_engineer.sectors import reference_sector_directions

    boundaries = [
        {
            "n": number,
            "name": "",
            "x": float(number * 50),
            "z": 200.0,
        }
        for number in range(1, boundary_count + 1)
    ]

    samples = {
        "dist": [float(i * 5) for i in range(61)],
        "pos_x": [float(i * 5) for i in range(61)],
        "pos_z": [200.0] * 61,
        "speed": [100.0] * 61,
    }

    directions = reference_sector_directions(samples, boundaries)

    assert directions is not None
    assert len(directions) == boundary_count

    for dx, dz in directions:
        assert dx == pytest.approx(1.0)
        assert dz == pytest.approx(0.0)

@pytest.mark.parametrize(
    "boundary_count",
    [1, 2, 3, 4],
    ids=["2-sectors", "3-sectors", "4-sectors", "5-sectors"],
)
def test_variable_sector_live_crossings(boundary_count: int) -> None:
    """Each authored boundary produces exactly its own sector crossing."""
    from app.race_engineer.sectors import LiveSectorTracker

    tracker = LiveSectorTracker()

    boundaries = [
        {
            "n": number,
            "name": "",
            "x": float(number * 50),
            "z": 200.0,
        }
        for number in range(1, boundary_count + 1)
    ]

    tracker.configure(
        boundaries,
        directions=[(1.0, 0.0)] * boundary_count,
    )

    for number in range(1, boundary_count + 1):
        x = float(number * 50)
        t = float(number * 10)

        assert tracker.observe(2, (x - 5, 200.0, t)) is None

        crossing = tracker.observe(
            2, (x + 5, 200.0, t + 0.1)
        )

        assert crossing is not None
        assert crossing.sector == number
        assert crossing.cumulative_ms == pytest.approx(
            number * 10000.0 + 50.0
        )

        expected_sector_ms = 10050.0 if number == 1 else 10000.0
        assert crossing.time_ms == pytest.approx(expected_sector_ms)

        # No duplicate announcement at the same boundary.
        assert tracker.observe(
            2, (x + 6, 200.0, t + 0.2)
        ) is None

@pytest.mark.parametrize(
    "boundary_count",
    [1, 2, 3, 4],
    ids=["2-sectors", "3-sectors", "4-sectors", "5-sectors"],
)
def test_variable_sector_finish_completion(boundary_count: int) -> None:
    """The final sector completes at the finish, exactly once."""
    from app.race_engineer.sectors import LiveSectorTracker

    boundaries = [
        {
            "n": number,
            "name": "",
            "x": float(number * 50),
            "z": 200.0,
        }
        for number in range(1, boundary_count + 1)
    ]
    directions = [(1.0, 0.0)] * boundary_count

    # An incomplete sequence cannot produce a final sector.
    incomplete = LiveSectorTracker()
    incomplete.configure(boundaries, directions=directions)
    incomplete.observe(2, (0.0, 200.0, 1.0))

    for number in range(1, boundary_count):
        x = float(number * 50)
        t = float(number * 10)
        incomplete.observe(2, (x - 5, 200.0, t))
        assert incomplete.observe(
            2, (x + 5, 200.0, t + 0.1)
        ) is not None

    finish_ms = (boundary_count + 1) * 10000.0 + 50.0
    assert incomplete.complete(2, finish_ms) is None

    # Every authored boundary must be crossed in order.
    tracker = LiveSectorTracker()
    tracker.configure(boundaries, directions=directions)

    for number in range(1, boundary_count + 1):
        x = float(number * 50)
        t = float(number * 10)
        assert tracker.observe(2, (x - 5, 200.0, t)) is None
        assert tracker.observe(
            2, (x + 5, 200.0, t + 0.1)
        ) is not None

    final = tracker.complete(2, finish_ms)

    assert final is not None
    assert final.sector == boundary_count + 1
    assert final.time_ms == pytest.approx(10000.0)
    assert final.cumulative_ms == pytest.approx(finish_ms)

    # Finishing the same lap again must not duplicate the callout.
    assert tracker.complete(2, finish_ms) is None

@pytest.mark.parametrize(
    ("sector_count", "sector_name"),
    [
        (2, "two"),
        (3, "three"),
        (4, "four"),
        (5, "five"),
    ],
)
def test_variable_sector_voice_announcements(
    sector_count: int, sector_name: str
) -> None:
    """Race Engineer announces the final sector for any valid layout."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager()
    manager.enabled = True
    manager.sector_voice_enabled = True
    manager.ctx.reference = object()
    manager._sector_reference_splits_ms = [
        10000.0
    ] * sector_count

    crossing = SectorCrossing(
        sector=sector_count,
        time_ms=10500.0,
        cumulative_ms=sector_count * 10000.0 + 500.0,
    )

    callout = manager.sector_callout(2, crossing)

    assert callout is not None
    assert callout.text == (
        f"Sector {sector_name}, "
        "point five zero zero seconds slower."
    )
    assert callout.metadata["sector"] == sector_count
    assert callout.metadata["reference_ms"] == 10000.0
    assert callout.metadata["delta_ms"] == 500.0

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary_count",
    [1, 2, 3, 4],
    ids=["2-sectors", "3-sectors", "4-sectors", "5-sectors"],
)
async def test_variable_sector_reference_preparation(
    boundary_count: int,
) -> None:
    """Prepare real boundaries, directions and splits from a recorded lap."""
    from datetime import UTC, datetime

    from app.processing.laps import CompletedLap, SessionInfo
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager()
    manager.on_session(
        SessionInfo(car_id=7, started_at="now"),
        session_id=1,
    )
    manager.ctx.track_name = "Test Circuit"

    samples = {
        "t": [i * 0.25 for i in range(13)],
        "dist": [i * 2.5 for i in range(13)],
        "pos_x": [i * 2.5 for i in range(13)],
        "pos_z": [0.0] * 13,
        "speed": [100.0] * 13,
    }

    authored = [
        {
            "n": number,
            "end": {"x": float(number * 5), "z": 0.0},
        }
        for number in range(1, boundary_count + 1)
    ]

    manager.sector_source = lambda track: authored

    lap = CompletedLap(
        number=1,
        time_ms=3000,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples=samples,
        fuel_start=10.0,
        fuel_end=9.0,
        full_lap=True,
    )
    lap.span_confirmed = True

    manager.on_lap(lap)
    await manager.refresh_reference()

    assert manager.ctx.reference is samples

    expected_boundaries = [
        {
            "n": number,
            "name": "",
            "x": float(number * 5),
            "z": 0.0,
        }
        for number in range(1, boundary_count + 1)
    ]

    assert manager._sector_tracker._boundaries == expected_boundaries

    directions = manager._sector_tracker._directions
    assert len(directions) == boundary_count

    for dx, dz in directions:
        assert dx == pytest.approx(1.0)
        assert dz == pytest.approx(0.0)

    splits = manager._sector_reference_splits_ms
    assert splits is not None
    assert len(splits) == boundary_count + 1

    expected_splits = (
        [500.0] * boundary_count
        + [3000.0 - boundary_count * 500.0]
    )

    assert splits == pytest.approx(expected_splits, abs=0.5)
    assert sum(splits) == pytest.approx(3000.0, abs=0.5)

@pytest.mark.parametrize(
    "invalid",
    [0, 4, -1, True, 1.5, "2"],
)
def test_sector_voice_rejects_invalid_decimals(invalid) -> None:
    """Only integer precision values 1, 2 and 3 are accepted."""
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager()
    manager.configure(sector_voice_decimals=2)

    with pytest.raises(ValueError, match="sector_voice_decimals"):
        manager.configure(sector_voice_decimals=invalid)

    assert manager.sector_voice_decimals == 2


@pytest.mark.parametrize(
    "invalid",
    ["nearest", "floor", "", "ROUND"],
)
def test_sector_voice_rejects_invalid_rounding(invalid: str) -> None:
    """Only round and truncate are accepted."""
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager()
    manager.configure(sector_voice_rounding="truncate")

    with pytest.raises(ValueError, match="sector_voice_rounding"):
        manager.configure(sector_voice_rounding=invalid)

    assert manager.sector_voice_rounding == "truncate"

@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "delta",
            "Sector one, point three seconds faster.",
        ),
        (
            "time",
            "Sector one, one point two seconds.",
        ),
        (
            "both",
            "Sector one, one point two seconds, "
            "point three seconds faster.",
        ),
    ],
)
def test_sector_voice_content_modes(
    content: str, expected: str
) -> None:
    """Sector voice can announce delta, sector time or both."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager(enabled=True)
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=1,
        sector_voice_content=content,
    )
    manager.ctx.reference = object()
    manager._sector_reference_splits_ms = [1500.0, 1500.0]

    crossing = SectorCrossing(
        sector=1,
        time_ms=1200.0,
        cumulative_ms=1200.0,
    )

    callout = manager.sector_callout(2, crossing)

    assert callout is not None
    assert callout.text == expected
    assert callout.metadata["time_ms"] == 1200.0
    assert callout.metadata["delta_ms"] == -300.0

@pytest.mark.parametrize(
    "invalid",
    ["difference", "sector", "all", "", "DELTA"],
)
def test_sector_voice_rejects_invalid_content(invalid: str) -> None:
    """Unsupported content modes must not replace the current setting."""
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager()
    assert manager.sector_voice_content == "delta"

    manager.configure(sector_voice_content="both")

    with pytest.raises(ValueError, match="sector_voice_content"):
        manager.configure(sector_voice_content=invalid)

    assert manager.sector_voice_content == "both"

@pytest.mark.parametrize(
    ("comparison", "sector_ms", "cumulative_ms", "expected", "delta_ms"),
    [
        (
            "individual", 30200.0, 59500.0,
            "Sector two, point two seconds slower.", 200.0,
        ),
        (
            "cumulative", 30200.0, 59500.0,
            "Sector two, point five seconds ahead.", -500.0,
        ),
        (
            "individual", 29800.0, 60700.0,
            "Sector two, point two seconds faster.", -200.0,
        ),
        (
            "cumulative", 29800.0, 60700.0,
            "Sector two, point seven seconds behind.", 700.0,
        ),
    ],
)
def test_sector_voice_comparison_modes(
    comparison: str,
    sector_ms: float,
    cumulative_ms: float,
    expected: str,
    delta_ms: float,
) -> None:
    """Compare either this sector or cumulative progress against the reference."""
    from app.race_engineer.manager import RaceEngineerManager
    from app.race_engineer.sectors import SectorCrossing

    manager = RaceEngineerManager(enabled=True)
    manager.configure(
        sector_voice_enabled=True,
        sector_voice_decimals=1,
        sector_voice_comparison=comparison,
    )

    manager.ctx.reference = object()
    manager._sector_reference_splits_ms = [
        30000.0, 30000.0, 30000.0,
    ]

    crossing = SectorCrossing(
        sector=2,
        time_ms=sector_ms,
        cumulative_ms=cumulative_ms,
    )

    callout = manager.sector_callout(2, crossing)

    assert callout is not None
    assert callout.text == expected
    assert callout.metadata["delta_ms"] == pytest.approx(delta_ms)

@pytest.mark.parametrize(
    "invalid",
    ["difference", "total", "sector", "", "CUMULATIVE"],
)
def test_sector_voice_rejects_invalid_comparison(invalid: str) -> None:
    """Unsupported comparison modes must not replace the current setting."""
    from app.race_engineer.manager import RaceEngineerManager

    manager = RaceEngineerManager()

    assert manager.sector_voice_comparison == "individual"

    manager.configure(sector_voice_comparison="cumulative")

    with pytest.raises(ValueError, match="sector_voice_comparison"):
        manager.configure(sector_voice_comparison=invalid)

    assert manager.sector_voice_comparison == "cumulative"

def test_spoken_sector_duration_minutes():
    """Sector durations use minutes; deltas retain decimal seconds."""
    from app.race_engineer.formatter import (
        spoken_sector_decimal,
        spoken_sector_duration,
    )

    assert spoken_sector_duration(65_300, 3, "truncate") == (
        "one minute five point three zero zero seconds"
    )
    assert spoken_sector_duration(65_300, 1, "round") == (
        "one minute five point three seconds"
    )
    assert spoken_sector_duration(60_000, 3, "round") == (
        "one minute zero point zero zero zero seconds"
    )
    assert spoken_sector_duration(120_005, 3, "round") == (
        "two minutes zero point zero zero five seconds"
    )
    assert spoken_sector_duration(59_995, 2, "round") == (
        "one minute zero point zero zero seconds"
    )
    assert spoken_sector_duration(59_995, 2, "truncate") == (
        "fifty-nine point nine nine seconds"
    )
    assert spoken_sector_decimal(65.3, 3, "truncate") == (
        "sixty-five point three zero zero"
    )
