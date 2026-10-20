"""Session identity survives inactive telemetry without merging separate races."""

import pytest

from app.models import SimulatorFlags
from app.processing.laps import (
    SALVAGE_WAIT_FRAMES,
    CompletedLap,
    LapProcessor,
    RaceResult,
    SessionInfo,
)
from app.telemetry.packet import build_packet, parse_packet

ON_TRACK = int(SimulatorFlags.CAR_ON_TRACK)
PAUSED = ON_TRACK | int(SimulatorFlags.PAUSED)
LOADING = int(SimulatorFlags.LOADING)


class Recording:
    def __init__(self) -> None:
        self.sessions: list[SessionInfo] = []
        self.laps: list[tuple[int, CompletedLap]] = []
        self.results: list[tuple[int, RaceResult]] = []
        self.processor = LapProcessor(self.on_lap, self.on_session, self.on_result, min_lap_ticks=1)
        self.pid = 0

    async def on_session(self, session: SessionInfo) -> None:
        self.sessions.append(session)

    async def on_lap(self, lap: CompletedLap) -> None:
        self.laps.append((len(self.sessions), lap))

    async def on_result(self, result: RaceResult) -> None:
        self.results.append((len(self.sessions), result))

    async def feed(
        self,
        lap: int,
        *,
        flags: int = ON_TRACK,
        car: int = 100,
        total: int = 11,
        last_ms: int = -1,
        position: int = 5,
    ) -> None:
        self.pid += 1
        await self.processor.feed(
            parse_packet(
                build_packet(
                    packet_id=self.pid,
                    current_lap=lap,
                    flags=flags,
                    car_id=car,
                    total_laps=total,
                    last_lap_time_ms=last_ms,
                    race_position=position,
                    total_positions=16,
                    speed_mps=30,
                )
            )
        )


@pytest.mark.parametrize("menu_lap", [-1, 0])
async def test_consecutive_races_through_menu_keep_separate_laps_and_results(menu_lap):
    r = Recording()
    for position in [5, 7]:
        await r.feed(1, total=1, position=position)
        await r.feed(1, total=1, position=position)
        await r.feed(2, total=1, last_ms=106_000, position=position)
        await r.feed(menu_lap, flags=0, total=0)
    assert len(r.sessions) == 2
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 1), (2, 1)]
    assert [(sid, result.final_position) for sid, result in r.results] == [(1, 5), (2, 7)]


@pytest.mark.parametrize(
    "flags, lap, car",
    [
        (0, 0, 100),
        (0, -1, 100),
        (0, 6, 0),
        (PAUSED, 0, 0),
        (LOADING, 0, 0),
    ],
)
async def test_inactive_pit_packets_preserve_session_and_lap_buffer(flags, lap, car):
    r = Recording()
    await r.feed(6)
    await r.feed(6)
    for _ in range(3):
        await r.feed(lap, flags=flags, car=car, total=0)
    await r.feed(6)
    await r.feed(7, last_ms=129_000)
    assert len(r.sessions) == 1
    assert [(sid, lap.number, lap.total_ticks) for sid, lap in r.laps] == [(1, 6, 3)]
    assert r.laps[0][1].samples["t"] == pytest.approx([1 / 120, 0.025, 0.0417], abs=1e-4)
    assert r.results == []


@pytest.mark.parametrize("flags, lap", [(0, 0), (PAUSED, 1), (ON_TRACK, -1)])
async def test_inactive_packets_do_not_open_empty_sessions(flags, lap):
    r = Recording()
    await r.feed(lap, flags=flags, car=0)
    assert r.sessions == []
    await r.feed(1)
    assert len(r.sessions) == 1
    assert r.sessions[0].car_id == 100


async def test_restart_after_negative_menu_lap_starts_new_session():
    r = Recording()
    await r.feed(6)
    await r.feed(-1, flags=0)
    await r.feed(1)
    await r.feed(2, last_ms=106_000)
    assert len(r.sessions) == 2
    assert [(sid, lap.number) for sid, lap in r.laps] == [(2, 1)]


async def test_car_change_is_confirmed_when_driving_resumes():
    r = Recording()
    await r.feed(6)
    await r.feed(-1, flags=0, car=200)
    assert len(r.sessions) == 1
    await r.feed(1, car=200)
    await r.feed(2, car=200, last_ms=106_000)
    assert [session.car_id for session in r.sessions] == [100, 200]
    assert [(sid, lap.car_id) for sid, lap in r.laps] == [(2, 200)]


async def test_qualifying_to_race_with_counter_reset():
    r = Recording()
    await r.feed(1, total=0)
    await r.feed(2, total=0, last_ms=106_000)
    await r.feed(-1, flags=0, total=0)
    await r.feed(1)
    await r.feed(2, last_ms=118_000)
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 1), (2, 1)]


async def test_replay_can_still_be_salvaged_when_menu_clears_lap_number():
    r = Recording()
    for _ in range(601):
        await r.feed(0, total=0)
    await r.feed(-1, flags=0, total=0, last_ms=10_000)
    # Held while driving is stopped (#153); lap 0 resuming is no stop mid-lap.
    assert r.laps == []
    await r.feed(0, total=0)
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 0, True)]
    assert len(r.sessions) == 2


async def test_off_track_finish_packet_still_saves_final_lap_and_result():
    r = Recording()
    await r.feed(11)
    await r.feed(11)
    await r.feed(12, flags=0, last_ms=106_000, position=7)
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 11)]
    assert [(sid, result.final_position) for sid, result in r.results] == [(1, 7)]
    await r.feed(12, flags=0, last_ms=106_000, position=8)
    assert len(r.results) == 1
    assert r.processor.live_lap_samples["t"] == []


@pytest.mark.parametrize("min_ticks, last_ms", [(60, 106_000), (1, -1)])
async def test_off_track_finish_preserves_result_when_final_lap_cannot_be_saved(min_ticks, last_ms):
    r = Recording()
    r.processor.min_lap_ticks = min_ticks
    await r.feed(11)
    await r.feed(12, flags=0, last_ms=last_ms, position=7)
    assert r.laps == []
    assert [(sid, result.final_position) for sid, result in r.results] == [(1, 7)]
    await r.feed(12, flags=0, last_ms=last_ms, position=8)
    assert len(r.results) == 1


async def test_pit_in_on_the_final_straight_keeps_the_session():
    """Pitting on a lap's last stretch: GT7 reports the line crossing into
    the next lap while the car is in the pit lane, where it is not "on
    track". The lap is salvaged from its GT7 time, and the drive goes on in
    the SAME session. Salvage used to end the session every time — right
    for a replay whose stream broke off, wrong for a pit stop."""
    r = Recording()
    for _ in range(600):
        await r.feed(6)
    for _ in range(3):
        await r.feed(7, flags=0, last_ms=10_000)
    assert r.laps == []  # held until driving resumes (#153)
    await r.feed(7)
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 6, True)]
    await r.feed(7)
    await r.feed(8, last_ms=106_000)
    assert len(r.sessions) == 1
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 6), (1, 7)]


async def test_replay_ending_in_a_menu_still_gets_its_own_session():
    """The counter jumping anywhere but the next lap is still a broken-off
    stream: what follows is a different stint."""
    r = Recording()
    for _ in range(600):
        await r.feed(3)
    await r.feed(0, flags=0, last_ms=10_000)
    await r.feed(1)
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 3, True)]
    assert len(r.sessions) == 2


@pytest.mark.parametrize("flags", [LOADING, 0])
@pytest.mark.parametrize("inactive_lap", [-1, 0, 6, 7])
async def test_pit_salvage_waits_for_driving_to_confirm_continuation(flags, inactive_lap):
    r = Recording()
    for _ in range(600):
        await r.feed(6)
    for _ in range(3):
        await r.feed(inactive_lap, flags=flags, total=0, last_ms=10_000)
    await r.feed(7, flags=PAUSED, total=0)
    await r.feed(7, last_ms=10_000)
    await r.feed(7)
    await r.feed(8, last_ms=106_000, total=7, position=3)
    assert len(r.sessions) == 1
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 6), (1, 7)]
    assert [(sid, result.final_position) for sid, result in r.results] == [(1, 3)]
    assert r.sessions[0].lap_count == 2


@pytest.mark.parametrize("flags", [LOADING, 0])
@pytest.mark.parametrize("next_lap, next_car", [(0, 100), (1, 100), (6, 200), (7, 200)])
async def test_salvage_still_separates_driving_that_does_not_continue(flags, next_lap, next_car):
    r = Recording()
    for _ in range(600):
        await r.feed(6)
    await r.feed(0, flags=flags, total=0, last_ms=10_000)
    await r.feed(next_lap, car=next_car)
    assert len(r.sessions) == 2
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 6, True)]
    assert r.sessions[-1].car_id == next_car


async def test_loading_salvage_at_finish_keeps_result_in_original_session():
    r = Recording()
    for _ in range(600):
        await r.feed(11)
    await r.feed(12, flags=LOADING, last_ms=10_000)
    for _ in range(3):
        await r.feed(12, flags=0, last_ms=10_000, position=7)
    assert len(r.sessions) == 1
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 11)]
    assert [(sid, result.final_position) for sid, result in r.results] == [(1, 7)]
    assert r.processor.live_lap_samples["t"] == []


async def test_loading_salvage_of_lap_zero_replay_does_not_merge_with_lap_one():
    r = Recording()
    for _ in range(600):
        await r.feed(0, total=0)
    await r.feed(0, flags=LOADING, total=0, last_ms=10_000)
    await r.feed(1, total=0)
    assert len(r.sessions) == 2
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 0)]


# --- a stop mid-lap: the pit box before the timing line (#153) ----------------


async def drive_to_the_box(r: Recording, *, box_flags: int = 0, box_lap: int = -1) -> None:
    """Laps 4 and 5 (5 in 10 s), then lap 6 to a pit box before the line:
    the in-lap is 10 s too, the very time GT7 still reports for lap 5. That
    is what made the stop a "salvaged" lap 6 carrying lap 5's time."""
    for _ in range(600):
        await r.feed(4)
    for _ in range(600):
        await r.feed(5, last_ms=12_000)
    for _ in range(600):
        await r.feed(6, last_ms=10_000)
    for _ in range(120):  # two seconds in the box
        await r.feed(box_lap, flags=box_flags, last_ms=10_000)


@pytest.mark.parametrize("box_flags, box_lap", [(0, -1), (0, 0), (LOADING, 6), (LOADING, -1)])
async def test_pit_box_before_the_line_keeps_the_lap_and_the_session(box_flags, box_lap):
    """Montreal: the box is before the timing line, so the car rejoins on the
    lap it stopped on. One session, no phantom lap, and lap 6 completes with
    GT7's own time and every sample from before the stop."""
    r = Recording()
    await drive_to_the_box(r, box_flags=box_flags, box_lap=box_lap)
    for _ in range(300):  # pit exit and the rest of lap 6
        await r.feed(6, last_ms=10_000)
    await r.feed(7, last_ms=128_168)
    assert len(r.sessions) == 1
    assert [(sid, lap.number, lap.time_ms, lap.salvaged) for sid, lap in r.laps] == [
        (1, 4, 12_000, False), (1, 5, 10_000, False), (1, 6, 128_168, False),
    ]
    assert r.laps[-1][1].total_ticks == 900
    assert r.sessions[0].lap_count == 3


async def test_pit_box_after_the_line_still_continues_on_the_next_lap():
    """The usual pit lane (#136): the counter steps while the car is in the
    box. The in-lap is salvaged, emitted when driving resumes, and the drive
    goes on in the same session."""
    r = Recording()
    for _ in range(600):
        await r.feed(6, last_ms=12_000)
    for _ in range(120):
        await r.feed(7, flags=0, last_ms=10_000)
    assert r.laps == []
    await r.feed(7, last_ms=10_000)
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 6, True)]
    for _ in range(600):
        await r.feed(7, last_ms=10_000)
    await r.feed(8, last_ms=11_000)
    assert len(r.sessions) == 1
    assert [(sid, lap.number) for sid, lap in r.laps] == [(1, 6), (1, 7)]


async def test_a_stop_longer_than_any_pit_stop_emits_the_salvage():
    """Menus, a replay that ended: past SALVAGE_WAIT_FRAMES the salvage is
    emitted as it used to be at once, and driving the same lap afterwards
    starts a new session."""
    r = Recording()
    await drive_to_the_box(r)
    assert r.laps[-1][1].number == 5  # the phantom has not appeared
    for _ in range(SALVAGE_WAIT_FRAMES):
        await r.feed(-1, flags=0, last_ms=10_000)
    assert [(lap.number, lap.time_ms, lap.salvaged) for _, lap in r.laps][-1] == (6, 10_000, True)
    assert r.processor.live_lap_samples["t"] == []
    await r.feed(6)
    assert len(r.sessions) == 2


async def test_a_packet_counter_reset_emits_the_salvage():
    """The console restarted its stream: whatever the stop was, it is over."""
    r = Recording()
    await drive_to_the_box(r)
    r.pid = 0
    await r.feed(-1, flags=0, last_ms=10_000)
    assert [(lap.number, lap.salvaged) for _, lap in r.laps][-1] == (6, True)


async def test_flush_emits_a_held_salvage_into_its_session():
    r = Recording()
    await drive_to_the_box(r)
    await r.processor.flush()
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps][-1] == (1, 6, True)
    await r.processor.flush()  # nothing held any more
    assert len(r.laps) == 3


async def test_the_held_salvage_is_built_from_a_copy_of_the_buffer():
    """Building a salvage trims columns in place; the buffer it came from
    must come out of a stop mid-lap whole."""
    r = Recording()
    await drive_to_the_box(r)
    buffer = r.processor.live_lap_samples
    assert len(buffer["t"]) == 600
    # Column lists the lap's build drops (packet A has no steering) are still
    # in the live buffer, and none of them was cut short.
    assert "steer" in buffer
    assert {len(v) for k, v in buffer.items() if v} == {600}


async def test_shutting_down_saves_a_held_salvage(tmp_path):
    """The service flushes on stop: a lap held through a stop the stream
    never came back from is still recorded."""
    from app.config import Settings
    from app.processing.cars import CarDatabase
    from app.service import TelemetryService
    from app.storage.db import init_db, make_engine, make_session_factory
    from app.storage.repository import Repository

    settings = Settings(source="udp", db_path=tmp_path / "test.db", telemetry_port=43741)
    engine = make_engine(settings.db_path)
    await init_db(engine)
    service = TelemetryService(settings, Repository(make_session_factory(engine)), CarDatabase())
    service.processor.min_lap_ticks = 1
    pid = 0

    async def feed(lap: int, flags: int = ON_TRACK, last_ms: int = -1) -> None:
        nonlocal pid
        pid += 1
        await service._on_packet(
            parse_packet(
                build_packet(
                    packet_id=pid, current_lap=lap, flags=flags, car_id=100,
                    last_lap_time_ms=last_ms, speed_mps=30,
                )
            )
        )

    for _ in range(600):
        await feed(1)
    for _ in range(600):
        await feed(2, last_ms=10_000)
    for _ in range(10):
        await feed(-1, flags=0, last_ms=10_000)
    assert service.session_id is not None
    before = [row["number"] for row in await service.repo.list_laps(service.session_id)]
    await service.stop()
    rows = await service.repo.list_laps(service.session_id)
    await engine.dispose()
    assert before == [1]
    assert sorted((row["number"], row["salvaged"]) for row in rows) == [(1, False), (2, True)]
