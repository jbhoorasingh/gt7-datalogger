"""Stint trend (#111): lap time and tyre temperature lap over lap."""

import pytest

from app.processing import stint
from app.processing.stint import LapFigures, drift, is_pit_lap, lap_figures, stint_trend


def row(
    number: int,
    time_ms: int,
    *,
    counts: bool = True,
    full: bool = True,
    reason: str = "",
    fuel: tuple[float, float] | None = None,
) -> dict:
    start, end = fuel if fuel is not None else (100.0 - number, 99.0 - number)
    return {
        "id": 100 + number,
        "number": number,
        "time_ms": time_ms,
        "counts_for_best": counts,
        "full_lap": full,
        "exclude_reason": reason,
        "fuel_start": start,
        "fuel_end": end,
    }


def figures(
    time_ms: int, front: float = 80.0, rear: float = 85.0, *, stopped_s: float = 0.0,
    fuel_added: float = 0.0, tyre_step: float = 0.0,
) -> LapFigures:
    avg = {"fl": front - 1, "fr": front + 1, "rl": rear - 1, "rr": rear + 1}
    return LapFigures(
        clock_s=time_ms / 1000 - stopped_s,
        fuel_added=fuel_added,
        tyre_avg=avg,
        tyre_max={"fl": front + 9, "fr": front + 11, "rl": rear + 9, "rr": rear + 11},
        tyre_step=tyre_step,
        tyre_first=dict(avg),
        tyre_last=dict(avg),
    )


def session(rows: list[dict], **overrides: LapFigures) -> dict:
    by_id = {r["id"]: figures(r["time_ms"]) for r in rows}
    for key, value in overrides.items():
        by_id[100 + int(key.removeprefix("lap"))] = value
    return stint_trend(rows, by_id)


# --- one lap's figures --------------------------------------------------------


def test_a_lap_is_reduced_to_its_clock_fuel_and_tyres() -> None:
    samples = {
        "t": [0.0, 1.0, 2.0, 3.0],
        "fuel": [50.0, 49.9, 49.8, 49.7],
        "tt_fl": [70.0, 80.0, 90.0, 80.0],
        "tt_fr": [72.0, 82.0, 92.0, 82.0],
        "tt_rl": [60.0, 60.0, 60.0, 60.0],
        "tt_rr": [61.0, 61.0, 61.0, 61.0],
    }
    f = lap_figures(samples)
    assert f is not None
    assert f.clock_s == pytest.approx(3.0)
    assert f.fuel_added == 0.0
    assert f.tyre_avg["fl"] == pytest.approx(80.0)
    assert f.tyre_max["fr"] == pytest.approx(92.0)


def test_a_lap_recorded_before_the_tyre_columns_has_no_temperatures() -> None:
    f = lap_figures({"t": [0.0, 1.0], "fuel": [50.0, 49.9]})
    assert f is not None and f.tyre_avg == {}
    out = stint_trend([row(1, 90_000)], {101: f})
    assert out["laps"][0]["tt_front"] is None
    assert out["laps"][0]["tt"] == {}


def test_a_lap_without_samples_has_no_figures() -> None:
    assert lap_figures({}) is None
    assert lap_figures({"t": []}) is None


def test_fuel_taken_on_is_every_rise_in_the_level() -> None:
    f = lap_figures({"t": [0.0, 1.0, 2.0, 3.0], "fuel": [20.0, 19.9, 35.0, 60.0]})
    assert f is not None
    assert f.fuel_added == pytest.approx(40.1)


# --- what a pit lap is --------------------------------------------------------


def test_an_ordinary_lap_is_not_a_pit_lap() -> None:
    assert not is_pit_lap(row(5, 90_000), figures(90_000), row(4, 90_100))
    # A race's first lap from the grid: GT7's time runs a second or two
    # ahead of the recording's clock, which is not a stop.
    assert not is_pit_lap(row(1, 95_000), figures(95_000, stopped_s=1.8), None)


def test_time_the_car_was_not_being_driven_is_a_stop() -> None:
    stopped = figures(125_000, stopped_s=stint.PIT_STOP_MIN_S + 20)
    assert is_pit_lap(row(5, 125_000), stopped, row(4, 90_000))


def test_fuel_going_up_is_a_stop() -> None:
    assert is_pit_lap(row(5, 110_000), figures(110_000, fuel_added=40.0), row(4, 90_000))
    # Taken on between the end of one lap and the start of the next.
    refuelled = row(5, 110_000, fuel=(80.0, 78.0))
    assert is_pit_lap(refuelled, figures(110_000), row(4, 90_000, fuel=(32.0, 30.0)))


def test_four_tyres_changing_temperature_at_once_is_a_stop() -> None:
    samples = {
        "t": [0.0, 1.0, 2.0, 3.0],
        "tt_fl": [96.0, 97.0, 62.0, 63.0],
        "tt_fr": [98.0, 98.5, 62.0, 63.0],
        "tt_rl": [104.0, 104.0, 62.0, 62.5],
        "tt_rr": [105.0, 105.5, 62.0, 62.5],
    }
    changed = lap_figures(samples)
    assert changed is not None
    assert changed.tyre_step == pytest.approx(35.0)
    assert is_pit_lap(row(5, 3_000), changed, row(4, 90_000))


def test_one_tyre_moving_fast_is_not_a_change_of_tyres() -> None:
    """A locked wheel, or one against a wall, moves twenty degrees in a
    sample. The other three do not."""
    samples = {
        "t": [0.0, 1.0, 2.0],
        "tt_fl": [80.0, 104.0, 103.0],
        "tt_fr": [81.0, 81.5, 82.0],
        "tt_rl": [85.0, 85.0, 85.5],
        "tt_rr": [86.0, 86.5, 86.5],
    }
    locked = lap_figures(samples)
    assert locked is not None
    assert locked.tyre_step < 1.0
    assert not is_pit_lap(row(5, 2_000), locked, row(4, 90_000))


def test_tyres_changed_between_two_laps_are_a_stop() -> None:
    worn = figures(90_000, front=95.0, rear=104.0)
    fresh = figures(99_000, front=62.0, rear=62.0)
    assert is_pit_lap(row(5, 99_000), fresh, row(4, 90_000), worn)
    # A front pair that cooled on a long straight is not a change of tyres.
    cooled = figures(90_500, front=83.0, rear=103.5)
    assert not is_pit_lap(row(5, 90_500), cooled, row(4, 90_000), worn)


def test_a_partial_lap_is_a_gap_and_not_a_stop() -> None:
    """Most partial laps are a car leaving the pits, but a lap cut short by
    a rewind is partial too, and nothing about it says which."""
    partial = row(5, 60_000, counts=False, full=False)
    assert not is_pit_lap(partial, figures(60_000), row(4, 90_000))


def test_a_lap_ruled_out_as_a_pit_out_lap_is_one() -> None:
    assert is_pit_lap(
        row(5, 99_000, counts=False, reason="pit-out"), figures(99_000), row(4, 90_000)
    )
    # Ruled out for anything else, it is a gap and not a stop.
    assert not is_pit_lap(
        row(5, 99_000, counts=False, reason="contact"), figures(99_000), row(4, 90_000)
    )


# --- the drift ---------------------------------------------------------------


def test_drift_is_the_slope_of_the_laps() -> None:
    fitted = drift([(float(n), 90_000.0 + 180.0 * n) for n in range(1, 9)])
    assert fitted is not None
    slope, intercept = fitted
    assert slope == pytest.approx(180.0)
    assert intercept == pytest.approx(90_000.0)


def test_one_bad_lap_does_not_move_the_drift() -> None:
    """A lap spent facing the wrong way is twenty seconds; a stint of tyre
    wear is two. A least-squares line through these says +0.80 s a lap."""
    points = [(float(n), 90_000.0 + 180.0 * n) for n in range(1, 11)]
    points[7] = (8.0, 112_000.0)
    fitted = drift(points)
    assert fitted is not None
    assert fitted[0] == pytest.approx(180.0, abs=1.0)


def test_two_laps_are_not_a_trend() -> None:
    assert drift([(1.0, 90_000.0), (2.0, 90_500.0)]) is None
    assert drift([]) is None


# --- a session ---------------------------------------------------------------


def test_a_session_without_a_stop_is_one_stint() -> None:
    rows = [row(n, 90_000 + 200 * n) for n in range(1, 8)]
    out = session(rows)
    assert [lap["stint"] for lap in out["laps"]] == [1] * 7
    assert len(out["stints"]) == 1
    one = out["stints"][0]
    assert (one["first_lap"], one["last_lap"], one["laps"]) == (1, 7, 7)
    assert one["pace_ms_per_lap"] == pytest.approx(200.0)
    assert one["pace_fit"] == [[1.0, 90_200.0], [7.0, 91_400.0]]


def test_laps_come_back_in_the_order_they_were_driven() -> None:
    rows = [row(n, 90_000) for n in (3, 1, 2)]
    assert [lap["number"] for lap in session(rows)["laps"]] == [1, 2, 3]


def test_a_stop_splits_the_session_and_belongs_to_neither_stint() -> None:
    rows = [row(n, 90_000 + 300 * n) for n in range(1, 6)]
    rows.append(row(6, 130_000))  # in, and stopped
    rows.append(row(7, 97_000, counts=False, reason="pit-out"))  # out
    rows += [row(n, 88_000 + 100 * n) for n in range(8, 13)]
    out = session(rows, lap6=figures(130_000, stopped_s=32.0))
    assert [lap["stint"] for lap in out["laps"]] == [1] * 5 + [None, None] + [2] * 5
    assert [lap["pit"] for lap in out["laps"]] == [False] * 5 + [True, True] + [False] * 5
    first, second = out["stints"]
    assert (first["first_lap"], first["last_lap"]) == (1, 5)
    assert (second["first_lap"], second["last_lap"]) == (8, 12)
    # Each stint's own drift: the stop, and the step between the two sets of
    # tyres, are in neither.
    assert first["pace_ms_per_lap"] == pytest.approx(300.0)
    assert second["pace_ms_per_lap"] == pytest.approx(100.0)


def test_a_lap_that_does_not_count_is_a_gap_in_its_stint() -> None:
    rows = [row(n, 90_000 + 200 * n) for n in range(1, 8)]
    rows[3] = row(4, 70_000, counts=False, reason="off-track")
    out = session(rows)
    assert [lap["stint"] for lap in out["laps"]] == [1] * 7
    assert out["laps"][3]["counts"] is False
    one = out["stints"][0]
    assert one["laps"] == 6
    assert one["pace_ms_per_lap"] == pytest.approx(200.0)


def test_a_session_that_begins_with_an_out_lap_begins_with_stint_one() -> None:
    rows = [row(1, 60_000, counts=False, full=False)]
    rows += [row(n, 90_000) for n in range(2, 6)]
    out = session(rows)
    assert [lap["stint"] for lap in out["laps"]] == [1] * 5
    assert [stint_["n"] for stint_ in out["stints"]] == [1]


def test_a_short_stint_has_no_drift() -> None:
    out = session([row(1, 90_000), row(2, 90_400)])
    one = out["stints"][0]
    assert one["pace_ms_per_lap"] is None
    assert one["pace_fit"] is None
    assert one["tt_front_per_lap"] is None


def test_tyre_temperatures_are_per_axle_and_drift_with_the_stint() -> None:
    rows = [row(n, 90_000) for n in range(1, 7)]
    overrides = {
        f"lap{n}": figures(90_000, front=70.0 + 2.0 * n, rear=75.0 + 3.5 * n)
        for n in range(1, 7)
    }
    out = session(rows, **overrides)
    assert out["laps"][0]["tt_front"] == pytest.approx(72.0)
    assert out["laps"][0]["tt_rear"] == pytest.approx(78.5)
    assert out["laps"][0]["tt"]["fl"] == {"avg": 71.0, "max": 81.0}
    one = out["stints"][0]
    assert one["tt_front_per_lap"] == pytest.approx(2.0)
    assert one["tt_rear_per_lap"] == pytest.approx(3.5)
