"""Session identity survives inactive telemetry without merging separate races."""

import pytest

from app.models import SimulatorFlags
from app.processing.laps import CompletedLap, LapProcessor, RaceResult, SessionInfo
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
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 0, True)]
    await r.feed(0, total=0)
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
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 6, True)]
    await r.feed(7)
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
    assert [(sid, lap.number, lap.salvaged) for sid, lap in r.laps] == [(1, 3, True)]
    await r.feed(1)
    assert len(r.sessions) == 2
