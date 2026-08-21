"""Lap detection, session boundaries, and lap metrics."""

import pytest

from app.models import SimulatorFlags, TelemetryPacket
from app.processing.laps import CompletedLap, LapProcessor, RaceResult, SessionInfo
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


def make_packet(**kwargs) -> TelemetryPacket:
    kwargs.setdefault("flags", ON_TRACK)
    return parse_packet(build_packet(**kwargs))


class Collector:
    def __init__(self) -> None:
        self.laps: list[CompletedLap] = []
        self.sessions: list[SessionInfo] = []
        self.results: list[RaceResult] = []

    async def on_lap(self, lap: CompletedLap) -> None:
        self.laps.append(lap)

    async def on_session(self, info: SessionInfo) -> None:
        self.sessions.append(info)

    async def on_result(self, result: RaceResult) -> None:
        self.results.append(result)


@pytest.fixture
def setup() -> tuple[LapProcessor, Collector]:
    c = Collector()
    return (
        LapProcessor(
            on_lap=c.on_lap,
            on_session=c.on_session,
            on_race_result=c.on_result,
            min_lap_ticks=1,
        ),
        c,
    )


async def feed_lap(proc: LapProcessor, lap_number: int, ticks: int, **kw) -> None:
    for _ in range(ticks):
        await proc.feed(make_packet(current_lap=lap_number, **kw))


async def test_lap_completion(setup) -> None:
    proc, c = setup
    await feed_lap(proc, 1, 120, speed_mps=50.0, throttle=255, fuel_level=100.0)
    # Crossing the line: counter advances, game reports last lap time
    await proc.feed(
        make_packet(current_lap=2, last_lap_time_ms=61_500, fuel_level=98.0, speed_mps=50.0)
    )
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.number == 1
    assert lap.time_ms == 61_500
    assert lap.total_ticks == 120
    assert lap.fuel_consumed == pytest.approx(2.0)
    assert lap.full_throttle_pct == pytest.approx(100.0)
    assert lap.max_speed == pytest.approx(180.0)


async def test_no_lap_on_first_boundary(setup) -> None:
    """Going from menu (lap 0) onto track (lap 1) must not emit a lap."""
    proc, c = setup
    await feed_lap(proc, 0, 10)
    await feed_lap(proc, 1, 10)
    assert c.laps == []


async def test_session_starts_once(setup) -> None:
    proc, c = setup
    await feed_lap(proc, 1, 10)
    await feed_lap(proc, 2, 10, last_lap_time_ms=60_000)
    assert len(c.sessions) == 1


async def test_new_session_on_car_change(setup) -> None:
    proc, c = setup
    await feed_lap(proc, 1, 10, car_id=100)
    await feed_lap(proc, 1, 10, car_id=200)
    assert len(c.sessions) == 2
    assert c.sessions[1].car_id == 200


async def test_new_session_on_lap_reset(setup) -> None:
    """Race restart: lap counter drops back to 1."""
    proc, c = setup
    await feed_lap(proc, 3, 10)
    await feed_lap(proc, 1, 10)
    assert len(c.sessions) == 2


async def test_new_session_on_lap_reset_to_zero(setup) -> None:
    """Race restart: lap counter drops back to 0 (out-lap)."""
    proc, c = setup
    await feed_lap(proc, 3, 10)
    await feed_lap(proc, 0, 10)
    assert len(c.sessions) == 2


async def test_paused_samples_not_recorded(setup) -> None:
    proc, _ = setup
    await feed_lap(proc, 1, 10)
    await feed_lap(proc, 1, 10, flags=ON_TRACK | int(SimulatorFlags.PAUSED))
    assert len(proc.live_lap_samples["t"]) == 10


async def test_coasting_and_metrics(setup) -> None:
    proc, c = setup
    # 50 ticks full throttle, 30 full brake, 20 coasting
    await feed_lap(proc, 1, 50, throttle=255, brake=0)
    await feed_lap(proc, 1, 30, throttle=0, brake=255)
    await feed_lap(proc, 1, 20, throttle=0, brake=0)
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=30_000))
    lap = c.laps[0]
    # abs tolerance: t is stored at 4 decimals, so time weights carry ~0.01%
    assert lap.full_throttle_pct == pytest.approx(50.0, abs=0.1)
    assert lap.full_brake_pct == pytest.approx(30.0, abs=0.1)
    assert lap.coasting_pct == pytest.approx(20.0, abs=0.1)


async def test_distance_integration(setup) -> None:
    proc, _ = setup
    # 60 ticks at 60 m/s = 1 second = 60 m
    await feed_lap(proc, 1, 60, speed_mps=60.0)
    assert proc.live_lap_samples["dist"][-1] == pytest.approx(60.0, abs=0.1)


async def test_no_duplicate_laps_while_save_is_slow() -> None:
    """Regression: with a real console, packets keep arriving while a
    completed lap is being persisted. A stale lap counter during that await
    used to re-trigger the boundary once per packet (dozens of identical
    lap rows saved within milliseconds)."""
    import asyncio

    collector = Collector()

    async def slow_on_lap(lap: CompletedLap) -> None:
        await asyncio.sleep(0.05)  # simulate the DB write
        collector.laps.append(lap)

    proc = LapProcessor(on_lap=slow_on_lap, on_session=collector.on_session, min_lap_ticks=1)
    await feed_lap(proc, 1, 30, speed_mps=50.0)

    # The boundary packet plus a burst of following packets, processed
    # concurrently the way the UDP path used to dispatch them.
    tasks = [
        asyncio.create_task(
            proc.feed(make_packet(current_lap=2, last_lap_time_ms=61_000, speed_mps=50.0))
        )
        for _ in range(10)
    ]
    await asyncio.gather(*tasks)
    assert len(collector.laps) == 1
    assert collector.laps[0].number == 1


async def test_phantom_lap_with_few_samples_is_discarded() -> None:
    """In menus/replays GT7's lap counter flickers through old values with a
    stale last_lap_time; a 'lap' with almost no samples must not be saved."""
    c = Collector()
    proc = LapProcessor(on_lap=c.on_lap, on_session=c.on_session)  # real threshold
    # A genuine lap: 700 ticks, then the boundary
    await feed_lap(proc, 1, 700, speed_mps=50.0)
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=61_000, speed_mps=50.0))
    assert len(c.laps) == 1
    # Counter flicker: 2 -> 1 (reset/new session) -> 2 again with a stale time
    await proc.feed(make_packet(current_lap=1, last_lap_time_ms=61_000))
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=61_000))
    assert len(c.laps) == 1  # no phantom zero-sample lap


async def test_no_samples_recorded_after_race_finish(setup) -> None:
    """After the checkered flag GT7 reports current_lap = total_laps + 1;
    the cool-down driving must not be recorded as a lap in progress."""
    proc, c = setup
    await feed_lap(proc, 5, 10, total_laps=5)
    await proc.feed(make_packet(current_lap=6, total_laps=5, last_lap_time_ms=59_000))
    assert len(c.laps) == 1  # final lap still completes
    await feed_lap(proc, 6, 20, total_laps=5, speed_mps=30.0)
    assert len(proc.live_lap_samples["t"]) == 0


# --- packet-loss-robust timing ----------------------------------------------


async def test_packet_gap_extends_time_axis(setup) -> None:
    proc, _ = setup
    for i in range(10):
        await proc.feed(make_packet(current_lap=1, packet_id=i, speed_mps=60.0))
    await proc.feed(make_packet(current_lap=1, packet_id=15, speed_mps=60.0))
    t = proc.live_lap_samples["t"]
    d = proc.live_lap_samples["dist"]
    assert t[-1] - t[-2] == pytest.approx(6 / 60)
    assert d[-1] - d[-2] == pytest.approx(60.0 * 6 / 60)
    assert proc.dropped_frames == 5


async def test_huge_gap_falls_back_to_one_frame(setup) -> None:
    proc, _ = setup
    for i in range(10):
        await proc.feed(make_packet(current_lap=1, packet_id=i, speed_mps=60.0))
    await proc.feed(make_packet(current_lap=1, packet_id=500, speed_mps=60.0))
    t = proc.live_lap_samples["t"]
    assert t[-1] - t[-2] == pytest.approx(1 / 60, abs=1e-3)


async def test_non_monotonic_pid_falls_back(setup) -> None:
    proc, _ = setup
    await proc.feed(make_packet(current_lap=1, packet_id=100, speed_mps=60.0))
    await proc.feed(make_packet(current_lap=1, packet_id=40, speed_mps=60.0))
    t = proc.live_lap_samples["t"]
    assert t == [0.0, pytest.approx(1 / 60, abs=1e-4)]


async def test_pause_does_not_inflate_time(setup) -> None:
    proc, _ = setup
    for i in range(10):
        await proc.feed(make_packet(current_lap=1, packet_id=i, speed_mps=60.0))
    paused = int(SimulatorFlags.CAR_ON_TRACK | SimulatorFlags.PAUSED)
    for i in range(10, 30):
        await proc.feed(make_packet(current_lap=1, packet_id=i, flags=paused))
    await proc.feed(make_packet(current_lap=1, packet_id=30, speed_mps=60.0))
    t = proc.live_lap_samples["t"]
    assert len(t) == 11  # paused packets are not sampled
    assert t[-1] - t[-2] == pytest.approx(1 / 60, abs=1e-3)  # pause added no time


# --- lap-clock cross-check (#20) ---------------------------------------------


def gt_ms(pid: int) -> int:
    """GT7's lap clock if it tracked real time perfectly: pid frames at 60 Hz."""
    return round(pid * 1000 / 60)


async def test_lap_clock_agrees_when_no_frames_dropped(setup) -> None:
    proc, _ = setup
    for i in range(10):
        await proc.feed(
            make_packet(current_lap=1, packet_id=i, speed_mps=60.0, fmt="C", lap_time_ms=gt_ms(i))
        )
    # First comparable packet is the baseline; the other 9 compare.
    assert proc.lap_clock_samples == 9
    assert abs(proc.lap_clock_drift_ms) <= 1  # rounding of gt_ms only
    assert proc.lap_clock_drift_worst_ms <= 1


async def test_lap_clock_counters_zero_below_packet_c(setup) -> None:
    proc, _ = setup
    await feed_lap(proc, 1, 10, speed_mps=60.0)  # fmt A: no lap_time_ms
    assert proc.lap_clock_samples == 0
    assert proc.lap_clock_drift_ms == 0
    assert proc.lap_clock_drift_worst_ms == 0


async def test_lap_clock_catches_uncredited_gap(setup, caplog) -> None:
    """A pid gap beyond MAX_FRAME_GAP is credited as one frame, but GT7's
    clock kept counting — the cross-check must surface the difference."""
    proc, _ = setup
    for i in range(10):
        await proc.feed(
            make_packet(current_lap=1, packet_id=i, speed_mps=60.0, fmt="C", lap_time_ms=gt_ms(i))
        )
    # 101-frame gap: integration falls back to 1 frame, GT7 says ~1.68 s more
    await proc.feed(
        make_packet(current_lap=1, packet_id=110, speed_mps=60.0, fmt="C", lap_time_ms=gt_ms(110))
    )
    # our t credited 10 frames; GT7 counted 110 -> ~100 frames of drift
    assert proc.lap_clock_drift_ms == pytest.approx(-100 * 1000 / 60, abs=2)
    assert proc.lap_clock_drift_worst_ms == pytest.approx(100 * 1000 / 60, abs=2)
    # Completing the lap logs one warning with the peak, then resets per-lap
    # state while the session worst survives.
    await proc.feed(
        make_packet(
            current_lap=2, packet_id=111, last_lap_time_ms=60_000, fmt="C", lap_time_ms=0
        )
    )
    assert any("drifted" in m and m.startswith("lap 1:") for m in caplog.messages)
    assert proc.lap_clock_drift_ms == 0
    assert proc.lap_clock_drift_worst_ms == pytest.approx(100 * 1000 / 60, abs=2)


async def test_lap_clock_rebaselines_after_pause(setup) -> None:
    """Ticks the sampler gates off (pause) freeze our t axis; the comparison
    must re-anchor afterwards instead of reporting the gap as drift."""
    proc, _ = setup
    for i in range(10):
        await proc.feed(
            make_packet(current_lap=1, packet_id=i, speed_mps=60.0, fmt="C", lap_time_ms=gt_ms(i))
        )
    paused = ON_TRACK | int(SimulatorFlags.PAUSED)
    for i in range(10, 30):
        await proc.feed(
            make_packet(current_lap=1, packet_id=i, flags=paused, fmt="C", lap_time_ms=gt_ms(i))
        )
    for i in range(30, 33):
        await proc.feed(
            make_packet(current_lap=1, packet_id=i, speed_mps=60.0, fmt="C", lap_time_ms=gt_ms(i))
        )
    assert abs(proc.lap_clock_drift_ms) <= 1
    assert proc.lap_clock_drift_worst_ms <= 1


async def test_metrics_time_weighted_under_drops(setup) -> None:
    proc, c = setup
    # 10 full-throttle frames (pids 0..9), then 10 coast samples every 3rd
    # frame (pids 12,15,...,39) -> throttle time 10 frames, coast 30 frames.
    for pid in range(10):
        await proc.feed(make_packet(current_lap=1, packet_id=pid, throttle=255, speed_mps=50.0))
    for pid in range(12, 40, 3):
        await proc.feed(make_packet(current_lap=1, packet_id=pid, throttle=0, speed_mps=50.0))
    await proc.feed(
        make_packet(current_lap=2, packet_id=40, last_lap_time_ms=60_000, speed_mps=50.0)
    )
    lap = c.laps[0]
    assert lap.full_throttle_pct == pytest.approx(100.0 * 10 / 40, abs=0.5)
    assert lap.coasting_pct == pytest.approx(100.0 * 30 / 40, abs=0.5)


# --- race position & the final result (#60) -----------------------------------


async def test_race_lap_records_position_and_channel(setup) -> None:
    proc, c = setup
    await feed_lap(proc, 1, 60, race_position=4, total_positions=8, total_laps=3)
    await proc.feed(
        make_packet(
            current_lap=2, last_lap_time_ms=61_000,
            race_position=4, total_positions=8, total_laps=3,
        )
    )
    lap = c.laps[0]
    assert lap.race_position == 4
    assert lap.samples["race_pos"] == [4.0] * 60


async def test_time_trial_keeps_no_position(setup) -> None:
    """GT7 sends -1 (and a field of 1) outside races: no per-lap position and
    no channel — 'no race' must never read as P-1 or chart as one."""
    proc, c = setup
    await feed_lap(proc, 1, 60)  # build_packet default: position 1 of 1
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=61_000))
    lap = c.laps[0]
    assert lap.race_position == -1
    assert "race_pos" not in lap.samples


async def test_position_channel_dropped_when_reporting_starts_mid_lap(setup) -> None:
    """A partial column would silently mis-align with dist; the lap keeps the
    scalar (last valid reading) and drops the channel."""
    proc, c = setup
    await feed_lap(proc, 1, 30)
    await feed_lap(proc, 1, 30, race_position=5, total_positions=8)
    await proc.feed(
        make_packet(current_lap=2, last_lap_time_ms=61_000, race_position=5, total_positions=8)
    )
    lap = c.laps[0]
    assert lap.race_position == 5
    assert "race_pos" not in lap.samples


async def test_finish_edge_commits_the_result_once(setup) -> None:
    proc, c = setup
    for lap in (1, 2, 3):
        await feed_lap(
            proc, lap, 60, race_position=3, total_positions=8, total_laps=3,
            last_lap_time_ms=61_000 if lap > 1 else -1,
        )
    # Checkered flag: the counter passes total_laps; cool-down keeps streaming.
    for _ in range(10):
        await proc.feed(
            make_packet(
                current_lap=4, total_laps=3, last_lap_time_ms=61_000,
                race_position=2, total_positions=8,
            )
        )
    assert len(c.results) == 1
    result = c.results[0]
    assert result.final_position == 2
    assert result.total_positions == 8
    assert result.race_laps == 3
    assert len(c.laps) == 3  # the flag packet also completed the final lap


async def test_finish_result_falls_back_to_last_valid_position(setup) -> None:
    """Some menus blank the position before the first past-finish packet; the
    result must carry the race's last real reading, not a -1."""
    proc, c = setup
    await feed_lap(proc, 3, 60, race_position=6, total_positions=12, total_laps=3)
    await proc.feed(
        make_packet(
            current_lap=4, total_laps=3, last_lap_time_ms=61_000,
            race_position=-1, total_positions=0,
        )
    )
    assert len(c.results) == 1
    assert c.results[0].final_position == 6
    assert c.results[0].total_positions == 12


async def test_race_without_position_reporting_claims_no_result(setup) -> None:
    """A lapped event where GT7 never reported positions must record nothing
    — 'no race result' stays distinct from finishing anywhere."""
    proc, c = setup
    await feed_lap(proc, 3, 60, total_laps=3)  # default: position 1 of 1
    await proc.feed(make_packet(current_lap=4, total_laps=3, last_lap_time_ms=61_000))
    assert c.results == []


async def test_stream_ending_mid_race_claims_no_result(setup) -> None:
    """No checkered flag, no result: a race the stream abandoned on lap 2
    must not claim a finishing position from wherever it happened to run."""
    proc, c = setup
    await feed_lap(proc, 2, 60, race_position=4, total_positions=8, total_laps=6)
    # Menu bounce / restart: counter resets, tearing the session down.
    await feed_lap(proc, 1, 5, race_position=4, total_positions=8, total_laps=6)
    assert c.results == []


async def test_second_race_gets_its_own_result(setup) -> None:
    """The finish edge re-arms with the session: a restart after the flag is
    a new race, not a continuation of the finished one."""
    proc, c = setup
    for final_pos in (2, 5):
        await feed_lap(
            proc, 1, 60, race_position=final_pos, total_positions=8, total_laps=1
        )
        await proc.feed(
            make_packet(
                current_lap=2, total_laps=1, last_lap_time_ms=61_000,
                race_position=final_pos, total_positions=8,
            )
        )
    assert [r.final_position for r in c.results] == [2, 5]
    assert len(c.sessions) == 2


# --- lap salvage: streams that end at the line (#26) --------------------------


LOADING = int(SimulatorFlags.LOADING)


async def test_salvage_single_lap_replay_on_reset(setup) -> None:
    """A replay ends AT the finish line: the counter never steps to prev+1,
    but GT7's reported time vouches for the buffered lap."""
    proc, c = setup
    await feed_lap(proc, 1, 300, speed_mps=50.0)
    gt = round(proc.live_lap_samples["t"][-1] * 1000)  # 299/60*1000 ~ 4983
    await proc.feed(make_packet(current_lap=0, last_lap_time_ms=gt, speed_mps=50.0))
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.number == 1
    assert lap.time_ms == gt
    assert lap.salvaged is True
    # The reset still opens a new session; the salvaged lap stays in the old.
    assert len(c.sessions) == 2
    assert c.sessions[0].lap_count == 1


async def test_salvage_requires_time_match(setup) -> None:
    """Without GT7's time agreeing, an abandoned buffer is just an aborted
    lap and must be discarded."""
    proc, c = setup
    await feed_lap(proc, 1, 300, speed_mps=50.0)
    await proc.feed(make_packet(current_lap=0, last_lap_time_ms=20_000, speed_mps=50.0))
    assert c.laps == []


async def test_salvage_on_loading(setup) -> None:
    """A replay's ending streams LOADING while the menu builds; the finished
    lap is salvaged from the buffer once, not once per LOADING packet."""
    proc, c = setup
    await feed_lap(proc, 1, 300, speed_mps=50.0)
    gt = round(proc.live_lap_samples["t"][-1] * 1000)
    await proc.feed(make_packet(current_lap=0, flags=LOADING, last_lap_time_ms=gt))
    assert len(c.laps) == 1
    assert c.laps[0].number == 1
    assert c.laps[0].salvaged is True
    for _ in range(5):
        await proc.feed(make_packet(current_lap=0, flags=LOADING, last_lap_time_ms=gt))
    assert len(c.laps) == 1


async def test_salvage_trims_preroll_with_lap_clock(setup) -> None:
    """The replay pre-roll before the line (GT7's lap clock frozen at 0) is
    trimmed off, so the salvaged lap starts at the crossing with t and dist
    rebased to 0."""
    proc, c = setup
    for _ in range(200):
        await proc.feed(make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=0))
    for i in range(400):
        await proc.feed(
            make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60))
        )
    gt = round(399 / 60 * 1000)  # duration of the flying lap alone
    await proc.feed(make_packet(flags=LOADING, last_lap_time_ms=gt, fmt="C"))
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.number == 0  # a later real lap 1 in this session must not collide
    assert lap.salvaged is True
    assert abs(lap.total_ticks - 400) <= 2
    assert lap.samples["t"][0] == 0.0
    assert lap.samples["dist"][0] == 0.0


async def test_out_lap_boundary_still_commits_nothing(setup) -> None:
    """Regression guard: sampling lap 0 must not let a real time-trial
    out-lap's 0 -> 1 boundary commit anything, even with a stale
    last_lap_time on the wire."""
    proc, c = setup
    for _ in range(300):
        await proc.feed(make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=0))
    await feed_lap(proc, 1, 10, speed_mps=50.0, last_lap_time_ms=61_000)
    assert c.laps == []


async def test_salvage_on_car_change(setup) -> None:
    """Result screen then a car swap: the finished lap's time arrives on
    off-track packets, and the salvaged lap must land in the OLD session
    with the OLD car."""
    proc, c = setup
    await feed_lap(proc, 1, 300, speed_mps=50.0, car_id=100)
    gt = round(proc.live_lap_samples["t"][-1] * 1000)
    for _ in range(3):
        await proc.feed(make_packet(current_lap=1, car_id=100, flags=0, last_lap_time_ms=gt))
    await proc.feed(make_packet(current_lap=0, car_id=200))
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.salvaged is True
    assert lap.car_id == 100
    assert lap.time_ms == gt
    assert len(c.sessions) == 2
    assert c.sessions[0].lap_count == 1
    assert c.sessions[1].lap_count == 0


async def test_salvage_splits_session_before_own_driving(setup) -> None:
    """Watch the leader's replay, then drive the same car: the user's laps
    must NOT land in the replay's session. With a lap-0 replay the counter
    never comes down from >0, so no lap_reset would ever separate them —
    the salvage itself has to end the session, or excluding the replay from
    bests would take the user's own laps with it (#26)."""
    proc, c = setup
    for i in range(300):
        await proc.feed(
            make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60))
        )
    gt = round(proc.live_lap_samples["t"][-1] * 1000)
    await proc.feed(make_packet(flags=LOADING, last_lap_time_ms=gt, fmt="C"))
    assert len(c.laps) == 1 and c.laps[0].salvaged is True
    salvaged_sessions = len(c.sessions)
    # Same car drives: out-lap 0, then a real lap 1 completed at the 1 -> 2
    # boundary. It must open its own session.
    await feed_lap(proc, 0, 10, speed_mps=50.0)
    await feed_lap(proc, 1, 300, speed_mps=50.0)
    await proc.feed(make_packet(current_lap=2, last_lap_time_ms=6_000, speed_mps=50.0))
    assert len(c.sessions) == salvaged_sessions + 1
    driven = c.laps[-1]
    assert driven.number == 1
    assert driven.salvaged is False
    assert c.sessions[-1].lap_count == 1


async def test_back_to_back_replay_salvages_get_own_sessions(setup) -> None:
    """Two single-lap replays in one sitting (same car, counter parked at 0)
    must produce two one-lap sessions — not one session with two laps both
    numbered 0, whose circuit label would belong to the first replay only."""
    proc, c = setup
    for cycle_ticks in (300, 320):
        for i in range(cycle_ticks):
            await proc.feed(
                make_packet(
                    current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60)
                )
            )
        gt = round((cycle_ticks - 1) * 1000 / 60)
        await proc.feed(make_packet(flags=LOADING, last_lap_time_ms=gt, fmt="C"))
    assert len(c.laps) == 2
    assert all(lap.salvaged for lap in c.laps)
    assert len(c.sessions) == 2
    assert [s.lap_count for s in c.sessions] == [1, 1]


async def test_salvage_survives_post_line_stub(setup) -> None:
    """A real console may stream a few ticks PAST the crossing before the
    LOADING cut; GT7's clock re-anchors there for a lap nobody will see.
    Salvage must still find the flying lap in front of the stub instead of
    trimming everything down to the stub and giving up."""
    proc, c = setup
    for _ in range(200):  # pre-roll, clock frozen
        await proc.feed(make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=0))
    for i in range(400):  # the flying lap, clock counting
        await proc.feed(
            make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60))
        )
    for i in range(30):  # past the line: clock re-anchored
        await proc.feed(
            make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60))
        )
    gt = round(399 / 60 * 1000)  # the flying lap's duration alone
    await proc.feed(make_packet(flags=LOADING, last_lap_time_ms=gt, fmt="C"))
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.salvaged is True
    assert lap.time_ms == gt
    assert abs(lap.total_ticks - 400) <= 2
    assert lap.samples["t"][0] == 0.0
    assert lap.samples["dist"][0] == 0.0


async def test_salvage_trims_frozen_final_frame_hold(setup) -> None:
    """The other post-line shape: the stream holds the last frame, GT7's
    clock frozen, while our t axis keeps integrating. Untrimmed, the lap
    would over-measure by the hold and miss the tolerance window."""
    proc, c = setup
    for i in range(400):
        await proc.feed(
            make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=round(i * 1000 / 60))
        )
    frozen = round(399 * 1000 / 60)
    for _ in range(120):  # ~2 s hold — well past max(500 ms, 0.5%) untrimmed
        await proc.feed(make_packet(current_lap=0, speed_mps=50.0, fmt="C", lap_time_ms=frozen))
    gt = round(399 / 60 * 1000)
    await proc.feed(make_packet(flags=LOADING, last_lap_time_ms=gt, fmt="C"))
    assert len(c.laps) == 1
    lap = c.laps[0]
    assert lap.salvaged is True
    assert lap.time_ms == gt
    assert abs(lap.total_ticks - 400) <= 2
