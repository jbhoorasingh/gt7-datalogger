"""Race Engineer: speech formatting, detector rules and manager gating."""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import SimulatorFlags, TelemetryPacket
from app.processing.laps import CompletedLap, SessionInfo
from app.processing.live_events import LiveEvent
from app.processing.strategy import LapFuel, project_strategy
from app.race_engineer.detectors.base import Sustained
from app.race_engineer.formatter import (
    spoken_decimal,
    spoken_distance,
    spoken_gap,
    spoken_int,
    spoken_lap_time,
    spoken_laps,
    spoken_position,
    spoken_speed,
    spoken_wheels,
)
from app.race_engineer.manager import RaceEngineerManager
from app.race_engineer.models import SPECS, categories_for
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


def packet(**kwargs: object) -> TelemetryPacket:
    kwargs.setdefault("flags", ON_TRACK)
    kwargs.setdefault("car_id", 7)  # matches completed_lap, so fuel history applies
    kwargs.setdefault("oil_pressure", 5.0)  # healthy engine unless a test says otherwise
    return parse_packet(build_packet(fmt="C", **kwargs))  # type: ignore[arg-type]


def completed_lap(
    number: int = 1,
    time_ms: int = 92_487,
    fuel_consumed: float = 2.0,
    counts_for_best: bool = True,
    **kwargs: object,
) -> CompletedLap:
    lap = CompletedLap(
        number=number,
        time_ms=time_ms,
        finished_at=datetime.now(UTC).isoformat(),
        car_id=7,
        samples={"t": [], "dist": []},
        fuel_start=10.0,
        fuel_end=10.0 - fuel_consumed,
        counts_for_best=counts_for_best,
    )
    lap.fuel_consumed = fuel_consumed
    lap.span_confirmed = True  # a lap the logger saw whole; kwargs can override
    for key, value in kwargs.items():
        setattr(lap, key, value)
    return lap


def manager(**kwargs: object) -> RaceEngineerManager:
    mgr = RaceEngineerManager(**kwargs)  # type: ignore[arg-type]
    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=1)
    return mgr


def feed(mgr: RaceEngineerManager, count: int, start: int = 0, **kwargs: object) -> list:
    """Drive `count` packets through the manager, returning every callout."""
    out = []
    for i in range(count):
        out += mgr.on_packet(packet(packet_id=start + i, **kwargs))
    return out


def types(callouts: list) -> list[str]:
    return [c.event_type for c in callouts]


# --- formatter ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("ms", "expected"),
    [
        (92_487, "one minute thirty-two point five"),
        (91_800, "one minute thirty-one point eight"),
        (60_000, "one minute"),
        (120_000, "two minutes"),
        (59_960, "one minute"),  # rounds up through the minute boundary
        (45_200, "forty-five point two seconds"),
        (61_040, "one minute one"),
        (0, "no time"),
    ],
)
def test_spoken_lap_time(ms: int, expected: str) -> None:
    assert spoken_lap_time(ms) == expected


def test_spoken_numbers() -> None:
    assert spoken_int(4) == "four"
    assert spoken_int(32) == "thirty-two"
    assert spoken_int(115) == "one hundred fifteen"
    assert spoken_decimal(5.24) == "five point two"
    assert spoken_decimal(5.0) == "five"
    assert spoken_decimal(1.05, places=2) == "one point zero five"


def test_spoken_plurals_and_labels() -> None:
    assert spoken_laps(5.24) == "five point two laps"
    assert spoken_laps(1.0) == "one lap"
    assert spoken_gap(300) == "three tenths"
    assert spoken_gap(-1200) == "one point two seconds"
    assert spoken_position(4) == "position four"
    assert spoken_wheels(["fl"]) == "front-left"
    assert spoken_wheels(["rl", "rr"]) == "rear"


# --- threshold helper --------------------------------------------------------


def test_sustained_needs_persistence_then_clears_with_hysteresis() -> None:
    s = Sustained(trigger=110.0, clear=106.0, hold_s=5.0)
    assert not s.update(115.0, 0.0)  # first sample only starts the clock
    assert not s.update(115.0, 4.9)
    assert s.update(115.0, 5.0)
    assert s.update(108.0, 6.0)  # below trigger but above clear: still latched
    assert not s.update(105.0, 7.0)  # cleared
    assert not s.update(115.0, 7.5)  # persistence starts over


def test_sustained_resets_when_the_value_dips_before_the_window_ends() -> None:
    s = Sustained(trigger=110.0, clear=106.0, hold_s=5.0)
    s.update(115.0, 0.0)
    s.update(100.0, 2.0)
    assert not s.update(115.0, 4.0)  # the earlier partial window doesn't count


# --- fuel projection (backend port of the frontend's projectStrategy) --------


def test_project_strategy_drops_partial_laps_and_other_cars() -> None:
    laps = [
        LapFuel(number=4, time_ms=60_000, fuel_consumed=0.4, car_id=7),  # out-lap
        LapFuel(number=3, time_ms=60_000, fuel_consumed=2.0, car_id=7),
        LapFuel(number=2, time_ms=60_000, fuel_consumed=2.0, car_id=7),
        LapFuel(number=1, time_ms=60_000, fuel_consumed=9.0, car_id=99),
    ]
    proj = project_strategy(10.0, 5, 7, "", laps)
    assert proj is not None
    assert proj.avg_fuel_per_lap == pytest.approx(2.0)
    assert proj.laps_to_empty == pytest.approx(5.0)
    assert proj.pit_before_lap == 10


def test_project_strategy_needs_a_lap_that_burned_fuel() -> None:
    assert project_strategy(10.0, 2, 7, "", []) is None
    assert (
        project_strategy(10.0, 2, 7, "", [LapFuel(1, 60_000, 0.0, 7)]) is None
    )


# --- lap / pace --------------------------------------------------------------


def test_lap_time_then_personal_best() -> None:
    mgr = manager()
    first = mgr.on_lap(completed_lap(number=1, time_ms=92_487))
    assert types(first) == ["lap_time"]
    assert first[0].text == "Lap time, one minute thirty-two point five."

    faster = mgr.on_lap(completed_lap(number=2, time_ms=91_800))
    assert types(faster) == ["personal_best"]
    assert faster[0].text.startswith("New personal best, one minute thirty-one point eight.")
    assert "faster" in faster[0].text

    slower = mgr.on_lap(completed_lap(number=3, time_ms=93_000))
    assert types(slower) == ["lap_time"]
    assert "slower" in slower[0].text


def test_partial_out_laps_are_never_announced() -> None:
    """GT7 reports a time for a pit out-lap; it is not a lap."""
    mgr = manager()
    assert mgr.on_lap(completed_lap(number=1, time_ms=40_000, counts_for_best=False)) == []
    # ...and it must not become the best either.
    assert mgr.ctx.best_lap_ms is None


def test_a_lap_re_flagged_partial_leaves_the_best_and_the_reference() -> None:
    mgr = manager()
    mgr.on_lap(completed_lap(number=1, time_ms=40_000))
    assert mgr.ctx.best_lap_ms == 40_000
    mgr.ctx.reference = {"dist": [0.0], "t": [0.0]}
    # Lap 2 proves lap 1 only covered part of the track.
    mgr.on_lap(
        completed_lap(
            number=2,
            time_ms=92_000,
            invalidated_best=True,
            partial_lap_numbers=[1],
            excluded_lap_numbers=[1],
        )
    )
    assert mgr.ctx.best_lap_ms == 92_000
    assert mgr.ctx.laps[-1].counts_for_best is False
    assert mgr.ctx.reference is None  # it came from the lap that was partial


def test_a_lap_ruled_out_by_hand_hands_the_reference_on() -> None:
    """#74: excluding the reference lap promotes the fastest lap still
    counting, and ruling it back in restores it."""
    mgr = manager()
    trace = {"t": [0.0, 1.0], "dist": [0.0, 40.0]}
    mgr.on_lap(completed_lap(number=1, time_ms=90_000, samples=dict(trace)))
    mgr.on_lap(completed_lap(number=2, time_ms=88_000, samples=dict(trace)))
    assert mgr._reference_lap == 2
    mgr.ctx.reference = mgr.ctx.laps[0].samples

    mgr.apply_best_override({2}, 90_000)
    assert mgr.ctx.best_lap_ms == 90_000
    assert [rec.counts_for_best for rec in mgr.ctx.laps] == [False, True]
    assert mgr.ctx.reference is None
    assert mgr._reference_lap == 1
    assert mgr._pending_reference is mgr.ctx.laps[1].samples

    mgr.apply_best_override(set(), 88_000)
    assert [rec.counts_for_best for rec in mgr.ctx.laps] == [True, True]
    assert mgr._reference_lap == 2


# --- race state --------------------------------------------------------------


def test_final_lap_fires_once_per_race() -> None:
    mgr = manager()
    out = feed(mgr, 5, current_lap=5, total_laps=5, speed_mps=40.0)
    assert types(out) == ["final_lap"]
    assert feed(mgr, 5, start=100, current_lap=5, total_laps=5, speed_mps=40.0) == []


def test_halfway_only_in_races_long_enough_to_have_a_middle() -> None:
    mgr = manager()
    assert feed(mgr, 3, current_lap=2, total_laps=3, speed_mps=40.0) == []
    long_race = manager()
    out = feed(long_race, 3, current_lap=6, total_laps=10, speed_mps=40.0)
    assert types(out) == ["race_halfway"]


def test_position_change_uses_the_shared_live_event_watcher() -> None:
    mgr = manager()
    out = mgr.on_live_event(
        LiveEvent(kind="overtake", position=4, previous_position=5, total_positions=12)
    )
    assert types(out) == ["position_gained"]
    assert out[0].text == "Position gained. You are now position four."
    # Same position again inside the cooldown is a duplicate.
    assert mgr.on_live_event(
        LiveEvent(kind="overtake", position=4, previous_position=5, total_positions=12)
    ) == []


# --- fuel --------------------------------------------------------------------


def test_fuel_remaining_waits_for_two_laps_of_history() -> None:
    mgr = manager()
    mgr.on_packet(packet(fuel_level=10.0, current_lap=2, speed_mps=40.0))
    assert types(mgr.on_lap(completed_lap(number=1, fuel_consumed=2.0))) == ["lap_time"]

    mgr.on_packet(packet(packet_id=1, fuel_level=8.0, current_lap=3, speed_mps=40.0))
    out = mgr.on_lap(completed_lap(number=2, time_ms=93_000, fuel_consumed=2.0))
    assert "fuel_remaining" in types(out)
    remaining = next(c for c in out if c.event_type == "fuel_remaining")
    assert remaining.text == "Fuel remaining, four laps."


def test_fuel_shortage_needs_two_consecutive_short_predictions() -> None:
    mgr = manager()
    # 10 laps left, ~4 laps of fuel: short by ~6.
    for lap in range(1, 4):
        mgr.on_packet(
            packet(
                packet_id=lap,
                fuel_level=8.0,
                current_lap=lap + 1,
                total_laps=12,
                speed_mps=40.0,
            )
        )
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap, fuel_consumed=2.0))
        if lap == 2:
            assert "fuel_short" not in types(out)  # first short prediction only
        if lap == 3:
            short = next(c for c in out if c.event_type == "fuel_short")
            assert short.text.startswith("Fuel will be short by")
            assert short.interrupt is True
            assert short.priority >= 90


def test_fuel_ignores_events_with_fuel_consumption_off() -> None:
    mgr = manager()
    mgr.on_packet(packet(fuel_capacity=0.0, fuel_level=100.0, current_lap=2, speed_mps=40.0))
    mgr.on_lap(completed_lap(number=1, fuel_consumed=0.0))
    mgr.on_packet(packet(packet_id=1, fuel_capacity=0.0, current_lap=3, speed_mps=40.0))
    out = mgr.on_lap(completed_lap(number=2, fuel_consumed=0.0))
    assert "fuel_remaining" not in types(out)


# --- engine ------------------------------------------------------------------


def test_water_temperature_persists_then_escalates_past_the_cooldown() -> None:
    mgr = manager()
    warm = feed(mgr, 60, water_temp=112.0, speed_mps=40.0, engine_rpm=6000.0)
    assert types(warm) == []  # 60 packets = 1 s, persistence is 5 s
    warm = feed(mgr, 360, start=100, water_temp=112.0, speed_mps=40.0, engine_rpm=6000.0)
    assert types(warm) == ["water_temp_high"]
    # Still high: the 20 s cooldown keeps it to a reminder, not a stream.
    assert feed(mgr, 60, start=500, water_temp=112.0, speed_mps=40.0) == []
    # Critical is an escalation and must not wait for the cooldown.
    hot = feed(mgr, 360, start=600, water_temp=125.0, speed_mps=40.0)
    assert types(hot) == ["water_temp_critical"]
    assert hot[0].interrupt is True


def test_engine_alerts_stay_quiet_off_track() -> None:
    mgr = manager()
    for i in range(600):
        mgr.on_packet(parse_packet(build_packet(fmt="C", packet_id=i, water_temp=130.0)))
    assert mgr.stats["emitted"] == 0


def test_oil_pressure_ignored_at_idle() -> None:
    mgr = manager()
    assert feed(mgr, 300, oil_pressure=0.5, engine_rpm=800.0, speed_mps=0.0) == []
    out = feed(mgr, 300, start=400, oil_pressure=0.5, engine_rpm=6000.0, speed_mps=40.0)
    assert types(out) == ["oil_pressure_low"]


# --- manager gating ----------------------------------------------------------


def test_categories_and_verbosity_filter_emissions() -> None:
    quiet = manager(verbosity="minimal")
    assert quiet.on_lap(completed_lap(number=1)) == []  # "lap" isn't in minimal
    assert quiet.stats["suppressed_category"] == 1

    no_lap = manager(categories={"engine"})
    assert no_lap.on_lap(completed_lap(number=1)) == []


def test_verbosity_presets_are_nested() -> None:
    assert categories_for("minimal") < categories_for("race")
    assert categories_for("race") < categories_for("coach")


def test_every_callout_carries_expiry_and_priority() -> None:
    mgr = manager()
    callout = mgr.on_lap(completed_lap(number=1))[0]
    spec = SPECS["lap_time"]
    assert callout.ttl_ms == spec.ttl_ms
    assert callout.expires_at_ms == callout.created_at_ms + spec.ttl_ms
    assert callout.priority == spec.priority
    assert callout.message_key == "lap.time"
    assert callout.message_args["time_ms"] == 92_487


def test_duplicate_dedupe_keys_are_suppressed() -> None:
    mgr = manager()
    lap = completed_lap(number=1)
    assert mgr.on_lap(lap) != []
    assert mgr.on_lap(lap) == []  # same session, same lap number
    assert mgr.stats["suppressed_duplicate"] == 1


def test_session_reset_clears_race_state_but_keeps_the_fuel_model() -> None:
    mgr = manager()
    mgr.on_packet(packet(fuel_level=10.0, current_lap=2, speed_mps=40.0))
    mgr.on_lap(completed_lap(number=1, fuel_consumed=2.0))
    feed(mgr, 1, current_lap=5, total_laps=5, speed_mps=40.0)  # final lap

    mgr.on_session(SessionInfo(car_id=7, started_at="now"), session_id=2)
    assert mgr.ctx.best_lap_ms is None
    assert len(mgr.ctx.laps) == 1  # a restart must not blank the fuel model
    # Final lap can be announced again — it's a different race.
    assert types(feed(mgr, 1, current_lap=5, total_laps=5, speed_mps=40.0)) == ["final_lap"]


def test_loading_screens_never_speak() -> None:
    mgr = manager()
    loading = int(SimulatorFlags.CAR_ON_TRACK | SimulatorFlags.LOADING)
    for i in range(600):
        mgr.on_packet(parse_packet(build_packet(fmt="C", packet_id=i, flags=loading,
                                                water_temp=130.0)))
    assert mgr.stats["emitted"] == 0


def test_test_callout_bypasses_every_gate() -> None:
    mgr = manager(categories=set())
    callout = mgr.test_callout("test", "Race engineer test callout.")
    assert callout.text == "Race engineer test callout."
    assert mgr.diagnostics()["last_callout"]["id"] == callout.id


def test_fuel_range_is_not_announced_when_the_tank_cannot_decide_anything() -> None:
    """"Fuel remaining, fifty-three laps" every lap of a practice stint is noise."""
    mgr = manager()
    for lap in (1, 2):
        mgr.on_packet(packet(packet_id=lap, fuel_level=100.0, current_lap=lap + 1,
                             speed_mps=40.0))
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap, fuel_consumed=1.0))
    assert "fuel_remaining" not in types(out)

    # Same range, but a race that is nearly as long: now it matters.
    race = manager()
    for lap in (1, 2):
        race.on_packet(packet(packet_id=lap, fuel_level=100.0, current_lap=lap + 1,
                              total_laps=100, speed_mps=40.0))
        out = race.on_lap(completed_lap(number=lap, time_ms=92_000 + lap, fuel_consumed=1.0))
    assert "fuel_remaining" in types(out)


# --- coaching ----------------------------------------------------------------


def _lockup(dist: float) -> dict[str, object]:
    return {"type": "lockup", "start_dist": dist, "end_dist": dist + 10,
            "wheels": ["fl"], "severity": 0.7}


def test_repeated_lockups_need_the_same_wheel_in_the_same_place() -> None:
    mgr = manager(verbosity="coach")
    mgr.ctx.corners = [
        {"n": 4, "entry_dist": 500.0, "exit_dist": 620.0, "apex_dist": 560.0},
    ]
    mgr.ctx.reference = {"dist": [0.0], "t": [0.0]}
    # Two laps with one lockup each: not yet a pattern.
    mgr.on_lap(completed_lap(number=1, events=[_lockup(430.0)]))
    out = mgr.on_lap(completed_lap(number=2, time_ms=92_000, events=[_lockup(440.0)]))
    assert "repeated_lockups" not in types(out)

    out = mgr.on_lap(completed_lap(number=3, time_ms=92_100, events=[_lockup(435.0)]))
    call = next(c for c in out if c.event_type == "repeated_lockups")
    # Braking events sit before the corner they belong to — it is still turn 4.
    assert call.text == "Repeated front-left lockups into turn four."
    assert call.metadata["count"] == 3


def test_lockups_far_from_any_corner_fall_back_to_the_braking_zone() -> None:
    mgr = manager(verbosity="coach")
    mgr.ctx.corners = [{"n": 4, "entry_dist": 2000.0, "exit_dist": 2100.0}]
    mgr.ctx.reference = {"dist": [0.0], "t": [0.0]}
    for lap in (1, 2, 3):
        out = mgr.on_lap(
            completed_lap(number=lap, time_ms=92_000 + lap, events=[_lockup(400.0 + lap)])
        )
    call = next(c for c in out if c.event_type == "repeated_lockups")
    assert call.text == "Repeated front-left lockups in the next braking zone."


def test_repeated_bottoming_is_chassis_feedback_not_coaching() -> None:
    bottoming = [
        {"type": "bottoming", "start_dist": 300.0, "end_dist": 310.0,
         "wheels": ["fl"], "severity": 0.99}
    ]
    mgr = manager(verbosity="coach")
    for lap in (1, 2, 3):
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap, events=bottoming))
    call = next(c for c in out if c.event_type == "repeated_bottoming")
    assert call.category == "chassis"

    # ...so switching coaching off still leaves setup feedback available.
    # Chassis notes live in the Coach preset, so verbosity has to allow them.
    chassis_only = manager(verbosity="coach", categories={"chassis"})
    for lap in (1, 2, 3):
        out = chassis_only.on_lap(
            completed_lap(number=lap, time_ms=92_000 + lap, events=bottoming)
        )
    assert types(out) == ["repeated_bottoming"]


# --- pace trend --------------------------------------------------------------


def _stint(mgr: RaceEngineerManager, times: list[int]) -> list:
    out: list = []
    for i, ms in enumerate(times, start=1):
        out = mgr.on_lap(completed_lap(number=i, time_ms=ms))
    return out


def test_pace_drop_needs_a_sustained_slide_not_one_slow_lap() -> None:
    mgr = manager()
    # Settled pace, then one slow lap: the average is still close to the best.
    out = _stint(mgr, [92_000, 92_100, 92_050, 92_600])
    assert "pace_drop" not in types(out)

    # Three laps averaging well over a second off the best is a real slump.
    out = mgr.on_lap(completed_lap(number=5, time_ms=93_400))
    out += mgr.on_lap(completed_lap(number=6, time_ms=93_500))
    call = next(c for c in out if c.event_type == "pace_drop")
    assert call.text.startswith("Your pace is dropping,")
    assert call.category == "pace"
    assert call.metadata["best_ms"] == 92_000


def test_pace_drop_speaks_again_only_when_it_deepens_or_returns() -> None:
    mgr = manager()
    _stint(mgr, [92_000, 92_100, 92_050, 93_400, 93_500, 93_400])
    # Same slump, no deeper: silence rather than a nag every lap.
    assert "pace_drop" not in types(mgr.on_lap(completed_lap(number=7, time_ms=93_450)))
    # Recovered...
    for lap, ms in ((8, 92_100), (9, 92_050), (10, 92_100)):
        mgr.on_lap(completed_lap(number=lap, time_ms=ms))
    # ...then slipping again: a new spell, so it is worth saying once more.
    out = []
    for lap, ms in ((11, 93_600), (12, 93_700), (13, 93_600)):
        out = mgr.on_lap(completed_lap(number=lap, time_ms=ms))
    assert "pace_drop" in types(out)


# --- corner detail -----------------------------------------------------------


def _corner_lap(
    brake_at: float, apex_speed: float, lost_ms: float = 0.0
) -> dict[str, list[float]]:
    """A synthetic lap: 5 m steps, braking from `brake_at`, slowest at the apex.

    `lost_ms` is dropped inside the corner (between entry and exit), which is
    what the delta series has to attribute to that corner.
    """
    dist = [i * 5.0 for i in range(200)]
    t = [d / 50.0 + (lost_ms / 1000 if d >= 560 else 0.0) for d in dist]
    speed = [apex_speed if 500 <= d <= 620 else 180.0 for d in dist]
    brake = [100.0 if brake_at <= d < 500 else 0.0 for d in dist]
    return {"dist": dist, "t": t, "speed": speed, "brake": brake}


def test_corner_loss_says_how_the_corner_was_driven_differently() -> None:
    mgr = manager(verbosity="coach")
    mgr.ctx.corners = [
        {"n": 6, "entry_dist": 500.0, "exit_dist": 620.0, "apex_dist": 560.0}
    ]
    mgr.ctx.reference = _corner_lap(brake_at=400.0, apex_speed=100.0)
    mgr.ctx.best_lap_ms = 92_000

    slow = _corner_lap(brake_at=380.0, apex_speed=95.0, lost_ms=300.0)
    call = next(
        c
        for c in mgr.on_lap(
            completed_lap(number=5, time_ms=92_600, samples=slow, session_best_before_ms=92_000)
        )
        if c.event_type == "corner_time_loss"
    )
    assert "in turn six" in call.text
    assert "braked twenty meters earlier" in call.text
    assert "carried five kilometers per hour less at the apex" in call.text
    assert call.metadata["brake_delta_m"] == -20.0


def test_corner_loss_falls_back_to_plain_wording_without_a_clear_cause() -> None:
    mgr = manager(verbosity="coach")
    mgr.ctx.corners = [
        {"n": 6, "entry_dist": 500.0, "exit_dist": 620.0, "apex_dist": 560.0}
    ]
    mgr.ctx.reference = _corner_lap(brake_at=400.0, apex_speed=100.0)
    mgr.ctx.best_lap_ms = 92_000

    # Same braking point and apex speed — the time went somewhere else.
    same = _corner_lap(brake_at=400.0, apex_speed=100.0, lost_ms=300.0)
    call = next(
        c
        for c in mgr.on_lap(
            completed_lap(number=5, time_ms=92_600, samples=same, session_best_before_ms=92_000)
        )
        if c.event_type == "corner_time_loss"
    )
    assert call.text.startswith("Most time was lost in turn six.")
    assert "braked" not in call.text


def test_spoken_units_follow_the_server_setting() -> None:
    assert spoken_distance(18) == "eighteen meters"
    assert spoken_distance(18, "imperial") == "fifty-nine feet"
    assert spoken_speed(5) == "five kilometers per hour"
    assert spoken_speed(8, "imperial") == "five miles per hour"


def test_pace_drop_stays_quiet_when_a_recent_lap_matched_the_best() -> None:
    """A personal best inside the window is the opposite of a slump."""
    mgr = manager()
    out = _stint(mgr, [93_000, 93_100, 93_050, 93_200, 92_000])
    assert "personal_best" in types(out)
    assert "pace_drop" not in types(out)


# --- coaching fidelity gate --------------------------------------------------


def _braking_lap(brake_at: float) -> dict[str, list[float]]:
    dist = [i * 5.0 for i in range(200)]
    return {
        "dist": dist,
        "t": [d / 50.0 for d in dist],
        "speed": [180.0 for _ in dist],
        "brake": [100.0 if brake_at <= d < 500 else 0.0 for d in dist],
    }


def _coach_ready(mgr: RaceEngineerManager, reference: dict[str, list[float]]) -> None:
    mgr.ctx.corners = [{"n": 4, "entry_dist": 500.0, "exit_dist": 620.0, "apex_dist": 560.0}]
    mgr.ctx.reference = reference


def test_coaching_stays_silent_until_the_laps_agree_on_the_distance() -> None:
    """A half-seen lap has its distance axis anchored elsewhere: every
    position-based comparison against it is fiction, so say nothing."""
    mgr = manager(verbosity="coach")
    _coach_ready(mgr, _braking_lap(brake_at=400.0))
    early = _braking_lap(brake_at=370.0)
    for lap in (1, 2):
        out = mgr.on_lap(
            completed_lap(number=lap, time_ms=92_000 + lap, samples=early,
                          span_confirmed=False, session_best_before_ms=92_000)
        )
    assert [c for c in out if c.category in ("coaching", "chassis")] == []

    # Same laps, once the track's distance is established.
    ready = manager(verbosity="coach")
    _coach_ready(ready, _braking_lap(brake_at=400.0))
    for lap in (1, 2):
        out = ready.on_lap(
            completed_lap(number=lap, time_ms=92_000 + lap, samples=early,
                          session_best_before_ms=92_000)
        )
    assert "braking_early" in types(out)


def test_braking_point_callout_needs_the_habit_not_the_moment() -> None:
    mgr = manager(verbosity="coach")
    _coach_ready(mgr, _braking_lap(brake_at=400.0))

    # One early lap, then one on the reference's marker: not a habit.
    mgr.on_lap(completed_lap(number=1, time_ms=92_100, samples=_braking_lap(370.0),
                             session_best_before_ms=92_000))
    out = mgr.on_lap(completed_lap(number=2, time_ms=92_200, samples=_braking_lap(400.0),
                                   session_best_before_ms=92_000))
    assert "braking_early" not in types(out)

    # Two laps braking 30 m early: worth a word.
    for lap in (3, 4):
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap,
                                       samples=_braking_lap(370.0),
                                       session_best_before_ms=92_000))
    call = next(c for c in out if c.event_type == "braking_early")
    assert call.text == "You are braking early into turn four, about thirty meters."
    assert call.category == "coaching"


def test_braking_point_reports_the_mildest_lap_not_the_worst() -> None:
    """Coach the habit the driver can trust, not their single worst moment."""
    mgr = manager(verbosity="coach")
    _coach_ready(mgr, _braking_lap(brake_at=400.0))
    for lap, brake_at in ((1, 330.0), (2, 375.0)):
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap,
                                       samples=_braking_lap(brake_at),
                                       session_best_before_ms=92_000))
    call = next(c for c in out if c.event_type == "braking_early")
    assert "twenty-five meters" in call.text  # the 25 m lap, not the 70 m one


def test_braking_late_is_reported_too() -> None:
    mgr = manager(verbosity="coach")
    _coach_ready(mgr, _braking_lap(brake_at=400.0))
    for lap in (1, 2):
        out = mgr.on_lap(completed_lap(number=lap, time_ms=92_000 + lap,
                                       samples=_braking_lap(430.0),
                                       session_best_before_ms=92_000))
    call = next(c for c in out if c.event_type == "braking_late")
    assert call.text == "You are braking late into turn four, about thirty meters."


def test_every_callout_type_is_documented_for_the_user() -> None:
    """A category toggle is guesswork unless the UI can say what it produces.

    The frontend catalog is mirrored by hand (as types.ts is); this fails the
    moment a new callout is added without telling anyone what it says.
    """
    catalog = (
        Path(__file__).resolve().parents[2]
        / "frontend" / "src" / "lib" / "calloutCatalog.ts"
    ).read_text()
    documented = set(re.findall(r'event:\s*"([a-z_]+)"', catalog))
    assert documented == set(SPECS), (
        f"undocumented: {sorted(set(SPECS) - documented)}; "
        f"stale: {sorted(documented - set(SPECS))}"
    )
