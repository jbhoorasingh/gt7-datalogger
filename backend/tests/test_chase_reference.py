"""Regression tests for Race Engineer Chase Reference selection."""

import pytest

from app.processing.laps import SessionInfo
from app.race_engineer.manager import RaceEngineerManager


def manager():
    mgr = RaceEngineerManager()
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    return mgr


def samples():
    return {"dist": [0.0, 100.0], "t": [0.0, 1.0]}


def test_historical_reference_selection():
    mgr = manager()
    historical = samples()

    assert mgr.reference_mode == "session_best"

    mgr.select_chase_reference(42, 90_000, historical)

    assert mgr.reference_mode == "chase"
    assert mgr.reference_lap_id == 42
    assert mgr.reference_time_ms == 90_000
    assert mgr._pending_reference is historical


@pytest.mark.parametrize(
    ("time_ms", "telemetry"),
    [
        (0, {"dist": [0.0]}),
        (-1, {"dist": [0.0]}),
        (90_000, {"dist": []}),
    ],
)
def test_unusable_historical_reference_is_rejected(time_ms, telemetry):
    mgr = manager()

    with pytest.raises(ValueError, match="no usable telemetry"):
        mgr.select_chase_reference(42, time_ms, telemetry)

    assert mgr.reference_mode == "session_best"


def test_chase_reference_can_be_reset_to_session_best():
    mgr = manager()
    mgr.select_chase_reference(42, 90_000, samples())

    mgr.select_session_best_reference()

    assert mgr.reference_mode == "session_best"
    assert mgr.reference_lap_id is None
    assert mgr.reference_time_ms is None
    assert mgr._chase_original_lap_id is None
    assert mgr._pending_reference is None


def test_invalidated_promoted_lap_restores_historical_reference():
    mgr = manager()
    historical = samples()
    promoted = samples()

    mgr.select_chase_reference(42, 90_000, historical)

    # Represent a valid, faster completed lap in the current session.
    from app.processing.laps import CompletedLap
    from datetime import UTC, datetime

    lap = CompletedLap(
        number=1,
        time_ms=89_000,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples=promoted,
        fuel_start=10.0,
        fuel_end=8.0,
        full_lap=True,
    )
    mgr.on_lap(lap)

    assert mgr.reference_time_ms == 89_000
    assert mgr.reference_lap_id is None

    mgr.apply_best_override({1}, None)

    assert mgr.reference_mode == "chase"
    assert mgr.reference_lap_id == 42
    assert mgr.reference_time_ms == 90_000
    assert mgr._pending_reference is historical


def make_lap(number, time_ms, telemetry, valid=True):
    """Construct a completed lap for Chase Reference tests."""
    from datetime import UTC, datetime
    from app.processing.laps import CompletedLap

    lap = CompletedLap(
        number=number,
        time_ms=time_ms,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples=telemetry,
        fuel_start=10.0,
        fuel_end=8.0,
        full_lap=valid,
    )
    lap.span_confirmed = valid
    return lap


def test_faster_valid_lap_promotes_reference():
    mgr = manager()
    historical = samples()
    faster = samples()

    mgr.select_chase_reference(42, 90_000, historical)
    mgr.on_lap(make_lap(1, 89_000, faster))

    assert mgr.reference_mode == "chase"
    assert mgr.reference_lap_id is None
    assert mgr.reference_time_ms == 89_000
    assert mgr._pending_reference is faster


def test_slower_lap_does_not_replace_reference():
    mgr = manager()
    historical = samples()
    slower = samples()

    mgr.select_chase_reference(42, 90_000, historical)
    mgr.on_lap(make_lap(1, 91_000, slower))

    assert mgr.reference_lap_id == 42
    assert mgr.reference_time_ms == 90_000
    assert mgr._pending_reference is historical


def test_invalid_lap_does_not_replace_reference():
    mgr = manager()
    historical = samples()
    invalid = samples()

    mgr.select_chase_reference(42, 90_000, historical)
    mgr.on_lap(make_lap(1, 88_000, invalid, valid=False))

    assert mgr.reference_lap_id == 42
    assert mgr.reference_time_ms == 90_000
    assert mgr._pending_reference is historical