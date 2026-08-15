"""Lap detection, session boundaries, and lap metrics."""

import pytest

from app.models import SimulatorFlags, TelemetryPacket
from app.processing.laps import CompletedLap, LapProcessor, SessionInfo
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)


def make_packet(**kwargs) -> TelemetryPacket:
    kwargs.setdefault("flags", ON_TRACK)
    return parse_packet(build_packet(**kwargs))


class Collector:
    def __init__(self) -> None:
        self.laps: list[CompletedLap] = []
        self.sessions: list[SessionInfo] = []

    async def on_lap(self, lap: CompletedLap) -> None:
        self.laps.append(lap)

    async def on_session(self, info: SessionInfo) -> None:
        self.sessions.append(info)


@pytest.fixture
def setup() -> tuple[LapProcessor, Collector]:
    c = Collector()
    return LapProcessor(on_lap=c.on_lap, on_session=c.on_session, min_lap_ticks=1), c


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
